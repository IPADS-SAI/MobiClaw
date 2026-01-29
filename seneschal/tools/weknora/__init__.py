# -*- coding: utf-8 -*-
"""WeKnora API 工具包。"""

from __future__ import annotations

import importlib

_agent = importlib.import_module("seneschal.tools.weknora.agent")
_chat = importlib.import_module("seneschal.tools.weknora.chat")
_chunk = importlib.import_module("seneschal.tools.weknora.chunk")
_evaluation = importlib.import_module("seneschal.tools.weknora.evaluation")
_faq = importlib.import_module("seneschal.tools.weknora.faq")
_knowledge = importlib.import_module("seneschal.tools.weknora.knowledge")
_kb = importlib.import_module("seneschal.tools.weknora.knowledge_base")
_search = importlib.import_module("seneschal.tools.weknora.knowledge_search")
_message = importlib.import_module("seneschal.tools.weknora.message")
_model = importlib.import_module("seneschal.tools.weknora.model")
_session = importlib.import_module("seneschal.tools.weknora.session")
_tag = importlib.import_module("seneschal.tools.weknora.tag")
_tenant = importlib.import_module("seneschal.tools.weknora.tenant")

create_agent = _agent.create_agent
delete_agent = _agent.delete_agent
get_agent = _agent.get_agent
list_agents = _agent.list_agents
copy_agent = _agent.copy_agent
update_agent = _agent.update_agent
list_agent_placeholders = _agent.list_agent_placeholders

knowledge_chat = _chat.knowledge_chat
agent_chat = _chat.agent_chat

delete_chunk = _chunk.delete_chunk
delete_all_chunks = _chunk.delete_all_chunks
list_chunks = _chunk.list_chunks

create_evaluation = _evaluation.create_evaluation
get_evaluation = _evaluation.get_evaluation

list_faq_entries = _faq.list_faq_entries
batch_import_faq_entries = _faq.batch_import_faq_entries
create_faq_entry = _faq.create_faq_entry
update_faq_entry = _faq.update_faq_entry
update_faq_status = _faq.update_faq_status
update_faq_tags = _faq.update_faq_tags
delete_faq_entries = _faq.delete_faq_entries
search_faq = _faq.search_faq

create_knowledge_from_file = _knowledge.create_knowledge_from_file
create_knowledge_from_url = _knowledge.create_knowledge_from_url
create_knowledge_manual = _knowledge.create_knowledge_manual
list_knowledge = _knowledge.list_knowledge
get_knowledge = _knowledge.get_knowledge
delete_knowledge = _knowledge.delete_knowledge
download_knowledge = _knowledge.download_knowledge
update_knowledge = _knowledge.update_knowledge
update_manual_knowledge = _knowledge.update_manual_knowledge
update_image_chunk = _knowledge.update_image_chunk
update_knowledge_tags = _knowledge.update_knowledge_tags
batch_get_knowledge = _knowledge.batch_get_knowledge

create_knowledge_base = _kb.create_knowledge_base
list_knowledge_bases = _kb.list_knowledge_bases
get_knowledge_base = _kb.get_knowledge_base
update_knowledge_base = _kb.update_knowledge_base
delete_knowledge_base = _kb.delete_knowledge_base
copy_knowledge_base = _kb.copy_knowledge_base
hybrid_search = _kb.hybrid_search

knowledge_search = _search.knowledge_search

load_messages = _message.load_messages
delete_message = _message.delete_message

create_model = _model.create_model
list_models = _model.list_models
get_model = _model.get_model
update_model = _model.update_model
delete_model = _model.delete_model
list_model_providers = _model.list_model_providers

create_session = _session.create_session
get_session = _session.get_session
list_sessions = _session.list_sessions
update_session = _session.update_session
delete_session = _session.delete_session
generate_session_title = _session.generate_session_title
continue_stream = _session.continue_stream

list_tags = _tag.list_tags
create_tag = _tag.create_tag
update_tag = _tag.update_tag
delete_tag = _tag.delete_tag

create_tenant = _tenant.create_tenant
get_tenant = _tenant.get_tenant
list_tenants = _tenant.list_tenants
update_tenant = _tenant.update_tenant
delete_tenant = _tenant.delete_tenant

__all__ = [
    "create_agent",
    "delete_agent",
    "get_agent",
    "list_agents",
    "copy_agent",
    "update_agent",
    "list_agent_placeholders",
    "knowledge_chat",
    "agent_chat",
    "delete_chunk",
    "delete_all_chunks",
    "list_chunks",
    "create_evaluation",
    "get_evaluation",
    "list_faq_entries",
    "batch_import_faq_entries",
    "create_faq_entry",
    "update_faq_entry",
    "update_faq_status",
    "update_faq_tags",
    "delete_faq_entries",
    "search_faq",
    "create_knowledge_from_file",
    "create_knowledge_from_url",
    "create_knowledge_manual",
    "list_knowledge",
    "get_knowledge",
    "delete_knowledge",
    "download_knowledge",
    "update_knowledge",
    "update_manual_knowledge",
    "update_image_chunk",
    "update_knowledge_tags",
    "batch_get_knowledge",
    "create_knowledge_base",
    "list_knowledge_bases",
    "get_knowledge_base",
    "update_knowledge_base",
    "delete_knowledge_base",
    "copy_knowledge_base",
    "hybrid_search",
    "knowledge_search",
    "load_messages",
    "delete_message",
    "create_model",
    "list_models",
    "get_model",
    "update_model",
    "delete_model",
    "list_model_providers",
    "create_session",
    "get_session",
    "list_sessions",
    "update_session",
    "delete_session",
    "generate_session_title",
    "continue_stream",
    "list_tags",
    "create_tag",
    "update_tag",
    "delete_tag",
    "create_tenant",
    "get_tenant",
    "list_tenants",
    "update_tenant",
    "delete_tenant",
]
