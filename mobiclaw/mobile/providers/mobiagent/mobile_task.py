# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import os
import json
import time
import logging
import base64
import io
import re
from typing import Dict, List, Optional
from PIL import Image
from openai import OpenAI

ANSI_RESET = "\033[0m"
ANSI_BOLD = "\033[1m"
ANSI_BLUE = "\033[94m"


def _highlight_log(message: str, color: str = ANSI_BLUE) -> str:
    """参考 orchestrator 的高亮风格，对日志消息做 ANSI 着色。"""
    return f"{ANSI_BOLD}{color}{message}{ANSI_RESET}"


class _BlueInfoLoggerAdapter(logging.LoggerAdapter):
    """仅将 info 级别日志渲染为蓝色，便于终端区分查看。"""

    @staticmethod
    def _enable_color() -> bool:
        if os.environ.get("NO_COLOR"):
            return False
        if os.environ.get("MOBIAGENT_INFO_BLUE", "1").strip() in {"0", "false", "False"}:
            return False
        return True

    def info(self, msg, *args, **kwargs):
        # 与 orchestrator 的上色策略保持一致：使用 _highlight_log 包装消息。
        if self._enable_color():
            msg = _highlight_log(str(msg), ANSI_BLUE)
        return self.logger.info(msg, *args, **kwargs)


# 使用模块级别的logger（级别由 setup_logging() 统一配置）
logger = _BlueInfoLoggerAdapter(logging.getLogger(__name__), {})

from ...base_task import BaseTask
from ...interrupts import ensure_not_interrupted, interruptible_sleep

from .load_md_prompt import load_prompt
from .prompts.decider_qwen3_e2e import (
    DECIDER_SYSTEM_PROMPT,
    DECIDER_USER_PROMPT,
    DECIDER_CURRENT_STEP_PROMPT,
)

DECIDER_INITIAL_TEMP = 0.1
DECIDER_TEMP_INCREMENT = 0.1
DECIDER_TIMEOUT = 30
DECIDER_MAX_TOKENS = 256

def _load_json_from_text(raw_text: str) -> Optional[Dict]:
    """尝试从混杂文本中提取 JSON；失败返回 None。"""
    if raw_text is None:
        return None
    if not isinstance(raw_text, str):
        raw_text = str(raw_text)

    text = raw_text.strip()

    def _try_load(candidate: str) -> Optional[Dict]:
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            return None

    parsed = _try_load(text)
    if parsed is not None:
        return parsed

    for pattern in [r"```json\s*([\s\S]*?)\s*```", r"```\s*([\s\S]*?)\s*```"]:
        match = re.search(pattern, text, re.MULTILINE)
        if match:
            parsed = _try_load(match.group(1).strip())
            if parsed is not None:
                return parsed

    normalized = text.replace("…", "...")
    if normalized != text:
        parsed = _try_load(normalized)
        if parsed is not None:
            return parsed
        text = normalized

    start_idx = text.find("{")
    if start_idx != -1:
        brace_count = 0
        for i in range(start_idx, len(text)):
            if text[i] == "{":
                brace_count += 1
            elif text[i] == "}":
                brace_count -= 1
                if brace_count == 0:
                    parsed = _try_load(text[start_idx:i + 1])
                    if parsed is not None:
                        return parsed

    return None


def _validate_action_parameters(decider_response: Dict):
    """校验不同动作的字段完整性。"""
    action = decider_response.get("action")
    parameters = decider_response.get("parameters", {})

    if not action:
        raise ValueError("Missing required field: 'action'")
    if not decider_response.get("reasoning"):
        raise ValueError("Missing required field: 'reasoning'")

    if action == "click":
        if not parameters.get("target_element"):
            raise ValueError("Click action missing required parameter: 'target_element'")
    elif action == "click_input":
        if not parameters.get("target_element"):
            raise ValueError("Click_input action missing required parameter: 'target_element'")
        if "text" not in parameters:
            raise ValueError("Click_input action missing required parameter: 'text'")
    elif action == "input":
        if "text" not in parameters:
            raise ValueError("Input action missing required parameter: 'text'")
    elif action == "swipe":
        direction = parameters.get("direction")
        if not direction:
            raise ValueError("Swipe action missing required parameter: 'direction'")
        if direction.upper() not in ["UP", "DOWN", "LEFT", "RIGHT"]:
            raise ValueError(f"Invalid swipe direction: '{direction}'")
    elif action == "done":
        if not parameters.get("status"):
            raise ValueError("Done action missing required parameter: 'status'")
    elif action == "wait":
        pass
    else:
        raise ValueError(f"Unknown action: '{action}'")


def _validate_decider_response(response_dict: Dict, use_e2e: bool = False):
    """校验 Decider 响应，包括 e2e 模式附加约束。"""
    _validate_action_parameters(response_dict)

    if use_e2e:
        action_name = response_dict["action"]
        parameters = response_dict.get("parameters", {})
        if action_name in ["click", "click_input"]:
            if "bbox" not in parameters or parameters["bbox"] is None:
                raise ValueError(f"E2E mode: {action_name} action missing required parameter: 'bbox'")
        elif action_name == "swipe":
            if "start_coords" in parameters and "end_coords" in parameters:
                if parameters["start_coords"] is None or parameters["end_coords"] is None:
                    logger.warning("E2E mode: swipe action has null start_coords or end_coords, will fall back to direction-based swipe")



class MobiAgentStepTask(BaseTask):
    """
    MobiAgent任务适配器（单步循环模式）
    使用框架的步骤循环，每次execute_step返回一步的动作
    """
    
    def __init__(
        self,
        task_description: str,
        device,
        data_dir: str,
        device_type: str = "Android",
        max_steps: int = 40,
        max_retries: int = 3,
        api_base: str = None,
        api_key: str = "",
        service_ip: str = "localhost",
        decider_port: int = 8000,
        grounder_port: int = 8001,
        planner_port: int = 8080,
        enable_planning: bool = False,
        use_e2e: bool = True,
        decider_model: str = "MobiMind-1.5-4B",
        grounder_model: str = "MobiMind-1.5-4B",
        planner_model: str = "Qwen3-VL-30B-A3B-Instruct",
        use_experience: bool = False,
        **kwargs
    ):
        """
        初始化MobiAgent任务
        
        Args:
            task_description: 任务描述
            device: 设备对象
            data_dir: 数据保存目录
            device_type: 设备类型
            max_steps: 最大步骤数
            max_retries: 最大重试次数
            service_ip: 服务IP地址
            decider_port: Decider模型端口
            grounder_port: Grounder模型端口
            planner_port: Planner模型端口
            enable_planning: 是否启用任务规划
            use_e2e: 是否使用端到端模式
            decider_model: Decider模型名称
            grounder_model: Grounder模型名称
            planner_model: Planner模型名称
            use_experience: 是否使用经验（调用planner改写任务）
        """
        super().__init__(
            task_description=task_description,
            device=device,
            data_dir=data_dir,
            device_type=device_type,
            max_steps=max_steps,
            max_retries=max_retries,
            use_step_loop=True,  # 启用步骤循环模式
            enable_planning=enable_planning,
            **kwargs
        )

        # 配置服务URL
        if api_base:
            if api_base.startswith("http://") or api_base.startswith("https://"):
                self.api_base = api_base.rstrip("/")
            else:
                logger.error("Invalid API base URL: %s", api_base)
                logger.error("API base URL must start with http:// or https://")
                raise ValueError(f"Invalid API base URL: {api_base}")
            decider_base_url = self.api_base
            grounder_base_url = self.api_base
            planner_base_url = self.api_base
            logger.debug("Using API base URL override: %s", self.api_base)
        elif service_ip is not None and (service_ip.startswith("http://") or service_ip.startswith("https://")):
            decider_base_url = f"{service_ip}:{decider_port}/v1"
            grounder_base_url = f"{service_ip}:{grounder_port}/v1"
            planner_base_url = f"{service_ip}:{planner_port}/v1"
            logger.debug("Using service IP: %s", service_ip)
        else:
            decider_base_url = f"http://{service_ip}:{decider_port}/v1"
            grounder_base_url = f"http://{service_ip}:{grounder_port}/v1"
            planner_base_url = f"http://{service_ip}:{planner_port}/v1"

        logger.debug(
            "MobiAgent endpoints resolved: decider=%s grounder=%s planner=%s",
            decider_base_url,
            grounder_base_url,
            planner_base_url,
        )
        
        # 初始化客户端
        self.decider_client = OpenAI(
            api_key=api_key or "0",
            base_url=decider_base_url,
        )
        
        self.grounder_client = OpenAI(
            api_key=api_key or "0",
            base_url=grounder_base_url,
        )
        
        self.planner_client = OpenAI(
            api_key=api_key or "0",
            base_url=planner_base_url,
        )
        
        self.decider_model = decider_model
        self.grounder_model = grounder_model
        self.planner_model = planner_model
        self.use_e2e = use_e2e
        self.use_experience = use_experience
        
        # 加载prompt模板
        prompt_dir = os.path.join(os.path.dirname(__file__), "prompts")
        if use_e2e:
            logger.debug("MobiAgent initialized with e2e mode")
        else:
            self.grounder_prompt_template_bbox = load_prompt("grounder_qwen3_bbox.md", prompt_dir)
            self.grounder_prompt_template_no_bbox = load_prompt("grounder_qwen3_coordinates.md", prompt_dir)
            logger.debug("MobiAgent initialized with decider+grounder mode")
        
        # 历史记录
        self.history = []
        
        # APP包名映射表
        self._init_app_package_mapping()
        
        logger.info("MobiAgentStepTask initialized")
    
    def _init_app_package_mapping(self):
        """初始化APP名称到包名的映射"""
        self.android_app_packages = {
            "携程": "ctrip.android.view",
            "同程": "com.tongcheng.android",
            "同程旅行": "com.tongcheng.android",
            "飞猪": "com.taobao.trip",
            "去哪儿": "com.Qunar",
            "华住会": "com.htinns",
            "饿了么/淘宝闪购": "me.ele",
            "支付宝": "com.eg.android.AlipayGphone",
            "淘宝": "com.taobao.taobao",
            "京东": "com.jingdong.app.mall",
            "美团": "com.sankuai.meituan",
            "美团外卖": "com.sankuai.meituan.takeoutnew",
            "滴滴出行": "com.sdu.didi.psnger",
            "微信": "com.tencent.mm",
            "微博": "com.sina.weibo",
            "华为商城": "com.vmall.client",
            "华为视频": "com.huawei.himovie",
            "华为音乐": "com.huawei.music",
            "华为应用市场": "com.huawei.appmarket",
            "拼多多": "com.xunmeng.pinduoduo",
            "大众点评": "com.dianping.v1",
            "小红书": "com.xingin.xhs",
            "浏览器": "com.microsoft.emmx",
            "QQ": "com.tencent.mobileqq",
            "知乎": "com.zhihu.android",
            "QQ音乐": "com.tencent.qqmusic",
            "网易云音乐": "com.netease.cloudmusic",
            "酷狗音乐": "com.kugou.android",
            "抖音": "com.ss.android.ugc.aweme",
            "快手": "com.smile.gifmaker",
            "哔哩哔哩": "tv.danmaku.bili",
            "爱奇艺": "com.qiyi.video",
            "腾讯视频": "com.tencent.qqlive",
            "优酷": "com.youku.phone",
            "高德地图": "com.autonavi.minimap",
            "百度地图": "com.baidu.BaiduMap",
            "闲鱼": "com.taobao.idlefish"
        }
        
        self.harmony_app_packages = {
            "携程": "com.ctrip.harmonynext",
            "携程旅行": "com.ctrip.harmonynext",
            "飞猪": "com.fliggy.hmos",
            "飞猪旅行": "com.fliggy.hmos",
            "IntelliOS": "ohos.hongmeng.intellios",
            "同程": "com.tongcheng.hmos",
            "饿了么/淘宝闪购": "me.ele.eleme",
            "知乎": "com.zhihu.hmos",
            "哔哩哔哩": "yylx.danmaku.bili",
            "微信": "com.tencent.wechat",
            "小红书": "com.xingin.xhs_hos",
            "QQ音乐": "com.tencent.hm.qqmusic",
            "高德地图": "com.amap.hmapp",
            "淘宝": "com.taobao.taobao4hmos",
            "微博": "com.sina.weibo.stage",
            "京东": "com.jd.hm.mall",
            "天气": "com.huawei.hmsapp.totemweather",
            "什么值得买": "com.smzdm.client.hmos",
            "闲鱼": "com.taobao.idlefish4ohos",
            "慧通差旅": "com.smartcom.itravelhm",
            "PowerAgent": "com.example.osagent",
            "航旅纵横": "com.umetrip.hm.app",
            "滴滴出行": "com.sdu.didi.hmos.psnger",
            "电子邮件": "com.huawei.hmos.email",
            "图库": "com.huawei.hmos.photos",
            "日历": "com.huawei.hmos.calendar",
            "心声社区": "com.huawei.it.hmxinsheng",
            "信息": "com.ohos.mms",
            "文件管理": "com.huawei.hmos.files",
            "运动健康": "com.huawei.hmos.health",
            "智慧生活": "com.huawei.hmos.ailife",
            "豆包": "com.larus.nova.hm",
            "WeLink": "com.huawei.it.welink",
            "设置": "com.huawei.hmos.settings",
            "懂车帝": "com.ss.dcar.auto",
            "美团外卖": "com.meituan.takeaway",
            "大众点评": "com.sankuai.dianping",
            "美团": "com.sankuai.hmeituan",
            "浏览器": "com.huawei.hmos.browser",
            "拼多多": "com.xunmeng.pinduoduo.hos"
        }
    
    def _get_package_name(self, app_name: str) -> Optional[str]:
        """根据APP名称获取包名"""
        if self.device_type == "Android":
            return self.android_app_packages.get(app_name)
        elif self.device_type == "Harmony":
            return self.harmony_app_packages.get(app_name)
        return None
    
    def _plan_task(self) -> Optional[Dict]:
        """
        任务规划：调用 planner 服务分析任务并确定目标应用
        
        Returns:
            包含 task_description, app_name, package_name 等信息的字典
        """
        try:
            # 加载 planner prompt 模板（根据设备类型选择）
            prompt_dir = os.path.join(os.path.dirname(__file__), "prompts")
            
            # 根据设备类型选择对应的 prompt
            if self.device_type == "Android":
                prompt_file = "planner_android.md"
            elif self.device_type == "Harmony":
                prompt_file = "planner_harmony.md"
            else:
                logger.warning(f"Unknown device type: {self.device_type}, using Android prompt")
                prompt_file = "planner_android.md"
            
            planner_prompt_path = os.path.join(prompt_dir, prompt_file)
            if os.path.exists(planner_prompt_path):
                planner_prompt_template = load_prompt(prompt_file, prompt_dir)
            else:
                # 使用默认的 planner prompt 模板
                logger.warning(f"Planner prompt {prompt_file} not found, using default")
                planner_prompt_template = self._get_default_planner_prompt()
            
            # 构建 prompt
            prompt = planner_prompt_template.format(
                task_description=self.original_task_description,
                experience_content="(No experience available)"  # 可以后续集成经验检索
            )
            
            logger.info(f"Calling planner service for {self.device_type} device...")
            start_time = time.time()
            
            # 调用 planner 服务
            response_str = self.planner_client.chat.completions.create(
                model=self.planner_model,
                messages=[
                    {
                        "role": "user",
                        "content": [{"type": "text", "text": prompt}],
                    }
                ],
                temperature=0.1,
                timeout=30,
            ).choices[0].message.content
            
            elapsed_time = time.time() - start_time
            logger.info(f"Planner time: {elapsed_time:.2f}s")
            logger.info(f"Planner response: {response_str}")
            
            # 解析响应
            response_json = self._parse_planner_response(response_str)
            
            if response_json is None:
                logger.warning("Failed to parse planner response")
                return None
            
            # 提取信息
            app_name = response_json.get("app_name")
            final_task_desc = response_json.get("final_task_description", self.original_task_description)
            package_name = response_json.get("package_name")
            
            if not app_name:
                logger.warning("Planner response missing app_name")
                return None
            
            # 本地匹配包名
            if not package_name:
                package_name = self._get_package_name(app_name)
            
            if not package_name:
                logger.warning(f"Package name not found for app: {app_name}")
                # 尝试常见的变体
                if app_name.endswith("旅行"):
                    alternative_name = app_name[:-2]
                    package_name = self._get_package_name(alternative_name)
                    if package_name:
                        logger.info(f"Found package using alternative name: {alternative_name}")
            
            if not package_name:
                logger.error(f"Cannot find package name for app: {app_name}")
                return None
            
            result = {
                "app_name": app_name,
                "package_name": package_name
            }
            
            # 如果启用 experience，使用 planner 优化后的任务描述
            if self.use_experience:
                result["task_description"] = final_task_desc
            
            logger.info(f"Planning result: App={app_name}, Package={package_name}")
            return result
            
        except Exception as e:
            logger.error(f"Planning failed: {e}", exc_info=True)
            return None
    
    def _parse_planner_response(self, response_str: str) -> Optional[Dict]:
        """解析 planner 响应（只包含 app_name 和 final_task_description）"""
        import re
        
        # 移除可能的代码块标记
        response_str = response_str.strip()
        if response_str.startswith("```json"):
            response_str = response_str[7:]
        elif response_str.startswith("```"):
            response_str = response_str[3:]
        
        if response_str.endswith("```"):
            response_str = response_str[:-3]
        
        response_str = response_str.strip()
        
        # 尝试直接解析
        try:
            return json.loads(response_str)
        except json.JSONDecodeError:
            pass
        
        # 尝试匹配 JSON 代码块
        pattern = re.compile(r"```json\s*(.*?)\s*```", re.DOTALL)
        match = pattern.search(response_str)
        
        if match:
            json_str = match.group(1).strip()
            try:
                return json.loads(json_str)
            except json.JSONDecodeError:
                pass
        
        # 尝试提取 JSON 对象
        start_idx = response_str.find('{')
        if start_idx != -1:
            end_idx = response_str.rfind('}')
            if end_idx > start_idx:
                json_str = response_str[start_idx:end_idx+1]
                try:
                    return json.loads(json_str)
                except json.JSONDecodeError:
                    pass
        
        logger.error(f"Failed to parse planner JSON response")
        return None
    
    def _get_default_planner_prompt(self) -> str:
        """获取默认的 planner prompt 模板"""
        return """You are a mobile task planner. Given a user task description, determine which app should be used and provide a refined task description.

Task: {task_description}

Experience: {experience_content}

Please respond in JSON format with the following structure:
{{
    "app_name": "<app name in Chinese>",
    "final_task_description": "<refined task description>"
}}

Important: Return ONLY the JSON object, no additional text or markdown formatting."""
    
    def execute_step(self, step_index: int) -> List[Dict]:
        """
        执行单步操作
        
        Args:
            step_index: 当前步骤索引
            
        Returns:
            动作序列列表
        """
        # 获取当前截图
        screenshot_path = os.path.join(self.data_dir, f"{step_index}.jpg")
        
        # 读取截图并转换为base64
        try:
            with open(screenshot_path, "rb") as f:
                image_data = f.read()
            encoded_image = base64.b64encode(image_data).decode('utf-8')
            img = Image.open(io.BytesIO(image_data))
        except Exception as e:
            logger.error(f"Failed to read screenshot: {e}")
            return [{"type": "retry", "params": {}}]
        
        messages = self._build_decider_messages(encoded_image)
        decider_response = self._call_decider(messages)
        
        if decider_response is None:
            logger.error("Decider调用失败，返回重试操作")
            return [{"type": "retry", "params": {}}]
        
        # 将响应添加到历史记录
        self.history.append(json.dumps(decider_response, ensure_ascii=False))
        
        # 解析操作
        action = decider_response.get("action")
        parameters = decider_response.get("parameters", {})
        reasoning = decider_response.get("reasoning", "")
        
        # logger.info(f"Action: {action}, Reasoning: {reasoning}")
        
        # 记录推理过程
        self._add_react(
            reasoning=reasoning,
            action=action,
            parameters=parameters,
            step_index=step_index
        )
        
        # 根据操作类型构建动作序列
        return self._build_action_sequence(action, parameters, reasoning, encoded_image, img)
    
    def _build_decider_messages(self, encoded_image: str) -> List[Dict]:
        """构建与 MobiAgent runner 一致的 Decider 多模态消息。"""
        history_str = "(No history)" if len(self.history) == 0 else "\n".join(
            f"{idx}. {h}" for idx, h in enumerate(self.history, 1)
        )

        context_text = DECIDER_USER_PROMPT.format(task=self.task_description, history=history_str)

        return [
            {
                "role": "system",
                "content": DECIDER_SYSTEM_PROMPT,
            },
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": context_text},
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{encoded_image}"}},
                    {"type": "text", "text": DECIDER_CURRENT_STEP_PROMPT},
                ],
            },
        ]

    def _call_model_with_validation_retry(
        self,
        client: OpenAI,
        model: str,
        messages: List[Dict],
        validator_func,
        max_attempts: int,
        max_tokens: int,
        context: str,
    ) -> Optional[Dict]:
        """统一模型调用：JSON解析 + 校验失败重试。"""
        temperature = DECIDER_INITIAL_TEMP
        for attempt in range(max_attempts):
            ensure_not_interrupted()
            try:
                start_time = time.time()
                response_str = client.chat.completions.create(
                    model=model,
                    messages=messages,
                    temperature=temperature,
                    timeout=DECIDER_TIMEOUT,
                    max_tokens=max_tokens,
                ).choices[0].message.content
                elapsed_time = time.time() - start_time
                logger.info(f"{context} time: {elapsed_time:.2f}s")
                logger.info(f"{context} response: {response_str}")
                parsed_response = self._parse_json_response(response_str)
                if parsed_response is None:
                    raise ValueError(f"{context} response is not valid JSON")
                validator_func(parsed_response)
                return parsed_response
            except Exception as e:
                temperature = DECIDER_INITIAL_TEMP + (attempt + 1) * DECIDER_TEMP_INCREMENT
                logger.error(f"{context}调用或校验失败 (attempt {attempt + 1}/{max_attempts}): {e}")
                if attempt < max_attempts - 1:
                    interruptible_sleep(2)
        return None

    def _call_decider(self, messages: List[Dict], max_attempts: Optional[int] = None) -> Optional[Dict]:
        """调用Decider模型并执行响应校验重试。"""
        attempts = max_attempts or max(1, self.max_retries)

        def _validator(response: Dict):
            _validate_decider_response(response, use_e2e=self.use_e2e)

        return self._call_model_with_validation_retry(
            client=self.decider_client,
            model=self.decider_model,
            messages=messages,
            validator_func=_validator,
            max_attempts=attempts,
            max_tokens=DECIDER_MAX_TOKENS,
            context="Decider",
        )
    
    def _call_grounder(self, encoded_image: str, prompt: str, max_attempts: int = 5) -> Optional[Dict]:
        """调用Grounder模型"""
        temperature = 0.0
        
        for attempt in range(max_attempts):
            ensure_not_interrupted()
            try:
                start_time = time.time()
                response_str = self.grounder_client.chat.completions.create(
                    model=self.grounder_model,
                    messages=[
                        {
                            "role": "user",
                            "content": [
                                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{encoded_image}"}},
                                {"type": "text", "text": prompt},
                            ]
                        }
                    ],
                    temperature=temperature,
                    timeout=30,
                    max_tokens=128,
                    response_format={"type": "json_object"}
                ).choices[0].message.content
                
                elapsed_time = time.time() - start_time
                logger.info(f"Grounder time: {elapsed_time:.2f}s")
                logger.info(f"Grounder response: {response_str}")
                
                return self._parse_json_response(response_str)
                
            except Exception as e:
                temperature = 0.1 + attempt * 0.1
                logger.error(f"Grounder调用失败 (attempt {attempt+1}/{max_attempts}): {e}")
                if attempt < max_attempts - 1:
                    interruptible_sleep(2)
        
        return None
    
    def _build_action_sequence(
        self,
        action: str,
        parameters: Dict,
        reasoning: str,
        encoded_image: str,
        img: Image.Image
    ) -> List[Dict]:
        """构建动作序列"""
        
        if action == "done":
            status = parameters.get("status", "success")
            return [{
                "type": "done",
                "params": {"status": status}
            }]
        
        elif action == "click":
            coords = self._get_click_coordinates(parameters, reasoning, encoded_image, img)
            if coords is None:
                return [{"type": "retry", "params": {}}]
            
            return [{
                "type": "click",
                "params": {"points": coords}
            }]
        
        elif action == "input":
            text = parameters.get("text", "")
            return [{
                "type": "input",
                "params": {"text": text}
            }]

        elif action == "click_input":
            text = parameters.get("text", "")
            bbox = parameters.get("bbox")

            if bbox is not None:
                bbox = self._convert_qwen3_coordinates(bbox, img.width, img.height, is_bbox=True)
                x1, y1, x2, y2 = bbox
                coords = [(x1 + x2) // 2, (y1 + y2) // 2]
            else:
                coords = self._get_click_coordinates(parameters, reasoning, encoded_image, img)

            if coords is None:
                return [{"type": "retry", "params": {}}]

            return [
                {
                    "type": "click",
                    "params": {"points": coords}
                },
                {
                    "type": "input",
                    "params": {"text": text}
                },
            ]
        
        elif action == "swipe":
            direction = parameters.get("direction")
            start_coords = parameters.get("start_coords")
            end_coords = parameters.get("end_coords")
            
            if start_coords and end_coords:
                start = self._convert_qwen3_coordinates(start_coords, img.width, img.height, is_bbox=False)
                end = self._convert_qwen3_coordinates(end_coords, img.width, img.height, is_bbox=False)
                return [{
                    "type": "swipe",
                    "params": {"points": [start[0], start[1], end[0], end[1]]}
                }]
            elif direction:
                return [{
                    "type": "swipe",
                    "params": {"direction": direction, "scale": 0.5}
                }]
        
        elif action == "wait":
            duration = parameters.get("duration", 2)
            # 框架已经有等待逻辑，这里返回空操作
            logger.info(f"Waiting {duration}s...")
            return []
        
        else:
            logger.warning(f"Unknown action: {action}")
            return [{"type": "retry", "params": {}}]
    
    def _get_click_coordinates(
        self,
        parameters: Dict,
        reasoning: str,
        encoded_image: str,
        img: Image.Image
    ) -> Optional[List[int]]:
        """获取点击坐标"""
        
        if self.use_e2e:
            # E2E模式：从decider直接获取bbox
            bbox = parameters.get("bbox")
            if bbox is None:
                logger.error("E2E mode: bbox not found")
                return None
            
            bbox = self._convert_qwen3_coordinates(bbox, img.width, img.height, is_bbox=True)
            x1, y1, x2, y2 = bbox
            return [(x1 + x2) // 2, (y1 + y2) // 2]
        
        else:
            # 非E2E模式：调用grounder
            target_element = parameters.get("target_element", "")
            bbox_flag = True
            
            if bbox_flag:
                grounder_prompt = self.grounder_prompt_template_bbox.format(
                    reasoning=reasoning,
                    description=target_element
                )
            else:
                grounder_prompt = self.grounder_prompt_template_no_bbox.format(
                    reasoning=reasoning,
                    description=target_element
                )
            
            grounder_response = self._call_grounder(encoded_image, grounder_prompt)
            
            if grounder_response is None:
                logger.error("Grounder调用失败")
                return None
            
            if bbox_flag:
                # 获取bbox
                bbox = None
                for key in grounder_response:
                    if key.lower() in ["bbox", "bbox_2d", "bbox-2d", "bbox_2d", "bbox2d"]:
                        bbox = grounder_response[key]
                        break
                
                if bbox is None:
                    logger.error("Grounder response missing bbox")
                    return None
                
                bbox = self._convert_qwen3_coordinates(bbox, img.width, img.height, is_bbox=True)
                x1, y1, x2, y2 = bbox
                return [(x1 + x2) // 2, (y1 + y2) // 2]
            else:
                # 获取坐标点
                coordinates = grounder_response.get("coordinates")
                if coordinates is None:
                    logger.error("Grounder response missing coordinates")
                    return None
                
                return self._convert_qwen3_coordinates(coordinates, img.width, img.height, is_bbox=False)
    
    def _convert_qwen3_coordinates(self, coords, img_width: int, img_height: int, is_bbox: bool = True):
        """转换Qwen3坐标（0-1000范围）到绝对坐标"""
        if is_bbox:
            x1, y1, x2, y2 = coords
            abs_x1 = int(x1 / 1000.0 * img_width)
            abs_y1 = int(y1 / 1000.0 * img_height)
            abs_x2 = int(x2 / 1000.0 * img_width)
            abs_y2 = int(y2 / 1000.0 * img_height)
            return [abs_x1, abs_y1, abs_x2, abs_y2]
        else:
            x, y = coords
            abs_x = int(x / 1000.0 * img_width)
            abs_y = int(y / 1000.0 * img_height)
            return [abs_x, abs_y]
    
    def _parse_json_response(self, response_str: str) -> Optional[Dict]:
        """解析JSON响应，支持代码块包裹的格式"""
        parsed = _load_json_from_text(response_str)
        if parsed is None:
            logger.error("JSON解析失败")
            logger.error(f"Response: {response_str}")
        return parsed
