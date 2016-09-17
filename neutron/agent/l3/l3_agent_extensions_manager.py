# Copyright (c) 2015 Mellanox Technologies, Ltd
# All Rights Reserved.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

from oslo_log import log

from neutron._i18n import _LE
from neutron.agent import agent_extensions_manager as agent_ext_manager

LOG = log.getLogger(__name__)


L3_AGENT_EXT_MANAGER_NAMESPACE = 'neutron.agent.l3.extensions'


def register_opts(conf):
    agent_ext_manager.register_opts(conf)


class L3AgentExtensionsManager(agent_ext_manager.AgentExtensionsManager):
    """Manage l3 agent extensions."""

    def __init__(self, conf):
        super(L3AgentExtensionsManager, self).__init__(conf,
                L3_AGENT_EXT_MANAGER_NAMESPACE)

    def add_router(self, context, data):
        """Notify all agent extensions to add router."""
        for extension in self:
            if hasattr(extension.obj, 'add_router'):
                extension.obj.add_router(context, data)
            else:
                LOG.error(
                    _LE("Agent Extension '%(name)s' does not "
                        "implement method add_router"),
                    {'name': extension.name}
                )

    def update_router(self, context, data):
        """Notify all agent extensions to update router."""
        for extension in self:
            if hasattr(extension.obj, 'update_router'):
                extension.obj.update_router(context, data)
            else:
                LOG.error(
                    _LE("Agent Extension '%(name)s' does not "
                        "implement method update_router"),
                    {'name': extension.name}
                )

    def delete_router(self, context, data):
        """Notify all agent extensions to delete router."""
        for extension in self:
            if hasattr(extension.obj, 'delete_router'):
                extension.obj.delete_router(context, data)
            else:
                LOG.error(
                    _LE("Agent Extension '%(name)s' does not "
                        "implement method delete_router"),
                    {'name': extension.name}
                )
