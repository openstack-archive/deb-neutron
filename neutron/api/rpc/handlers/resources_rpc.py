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

import collections

from neutron_lib import exceptions
from oslo_log import helpers as log_helpers
import oslo_messaging

from neutron._i18n import _
from neutron.api.rpc.callbacks.consumer import registry as cons_registry
from neutron.api.rpc.callbacks.producer import registry as prod_registry
from neutron.api.rpc.callbacks import resources
from neutron.api.rpc.callbacks import version_manager
from neutron.common import constants
from neutron.common import rpc as n_rpc
from neutron.common import topics
from neutron.objects import base as obj_base


class ResourcesRpcError(exceptions.NeutronException):
    pass


class InvalidResourceTypeClass(ResourcesRpcError):
    message = _("Invalid resource type %(resource_type)s")


class ResourceNotFound(ResourcesRpcError):
    message = _("Resource %(resource_id)s of type %(resource_type)s "
                "not found")


def _validate_resource_type(resource_type):
    if not resources.is_valid_resource_type(resource_type):
        raise InvalidResourceTypeClass(resource_type=resource_type)


def resource_type_versioned_topic(resource_type, version=None):
    """Return the topic for a resource type.

    If no version is provided, the latest version of the object will
    be used.
    """
    _validate_resource_type(resource_type)
    cls = resources.get_resource_cls(resource_type)
    return topics.RESOURCE_TOPIC_PATTERN % {'resource_type': resource_type,
                                            'version': version or cls.VERSION}


class ResourcesPullRpcApi(object):
    """Agent-side RPC (stub) for agent-to-plugin interaction.

    This class implements the client side of an rpc interface.  The server side
    can be found below: ResourcesPullRpcCallback.  For more information on
    this RPC interface, see doc/source/devref/rpc_callbacks.rst.
    """

    def __new__(cls):
        # make it a singleton
        if not hasattr(cls, '_instance'):
            cls._instance = super(ResourcesPullRpcApi, cls).__new__(cls)
            target = oslo_messaging.Target(
                topic=topics.PLUGIN, version='1.0',
                namespace=constants.RPC_NAMESPACE_RESOURCES)
            cls._instance.client = n_rpc.get_client(target)
        return cls._instance

    @log_helpers.log_method_call
    def pull(self, context, resource_type, resource_id):
        _validate_resource_type(resource_type)

        # we've already validated the resource type, so we are pretty sure the
        # class is there => no need to validate it specifically
        resource_type_cls = resources.get_resource_cls(resource_type)

        cctxt = self.client.prepare()
        primitive = cctxt.call(context, 'pull',
            resource_type=resource_type,
            version=resource_type_cls.VERSION, resource_id=resource_id)

        if primitive is None:
            raise ResourceNotFound(resource_type=resource_type,
                                   resource_id=resource_id)

        return resource_type_cls.clean_obj_from_primitive(primitive)


class ResourcesPullRpcCallback(object):
    """Plugin-side RPC (implementation) for agent-to-plugin interaction.

    This class implements the server side of an rpc interface.  The client side
    can be found above: ResourcesPullRpcApi.  For more information on
    this RPC interface, see doc/source/devref/rpc_callbacks.rst.
    """

    # History
    #   1.0 Initial version

    target = oslo_messaging.Target(
        version='1.0', namespace=constants.RPC_NAMESPACE_RESOURCES)

    def pull(self, context, resource_type, version, resource_id):
        obj = prod_registry.pull(resource_type, resource_id, context=context)
        if obj:
            return obj.obj_to_primitive(target_version=version)


class ResourcesPushToServersRpcApi(object):
    """Publisher-side RPC (stub) for plugin-to-plugin fanout interaction.

    This class implements the client side of an rpc interface.  The receiver
    side can be found below: ResourcesPushToServerRpcCallback.  For more
    information on this RPC interface, see doc/source/devref/rpc_callbacks.rst.
    """

    def __init__(self):
        target = oslo_messaging.Target(
            topic=topics.SERVER_RESOURCE_VERSIONS, version='1.0',
            namespace=constants.RPC_NAMESPACE_RESOURCES)
        self.client = n_rpc.get_client(target)

    @log_helpers.log_method_call
    def report_agent_resource_versions(self, context, agent_type, agent_host,
                                       version_map):
        """Fan out all the agent resource versions to other servers."""
        cctxt = self.client.prepare(fanout=True)
        cctxt.cast(context, 'report_agent_resource_versions',
                   agent_type=agent_type,
                   agent_host=agent_host,
                   version_map=version_map)


class ResourcesPushToServerRpcCallback(object):
    """Receiver-side RPC (implementation) for plugin-to-plugin interaction.

    This class implements the receiver side of an rpc interface.
    The client side can be found above: ResourcePushToServerRpcApi.  For more
    information on this RPC interface, see doc/source/devref/rpc_callbacks.rst.
    """

    # History
    #   1.0 Initial version

    target = oslo_messaging.Target(
        version='1.0', namespace=constants.RPC_NAMESPACE_RESOURCES)

    @log_helpers.log_method_call
    def report_agent_resource_versions(self, context, agent_type, agent_host,
                                       version_map):
        consumer_id = version_manager.AgentConsumer(agent_type=agent_type,
                                                    host=agent_host)
        version_manager.update_versions(consumer_id, version_map)


class ResourcesPushRpcApi(object):
    """Plugin-side RPC for plugin-to-agents interaction.

    This interface is designed to push versioned object updates to interested
    agents using fanout topics.

    This class implements the caller side of an rpc interface.  The receiver
    side can be found below: ResourcesPushRpcCallback.
    """

    def __init__(self):
        target = oslo_messaging.Target(
            namespace=constants.RPC_NAMESPACE_RESOURCES)
        self.client = n_rpc.get_client(target)

    def _prepare_object_fanout_context(self, obj, resource_version,
                                       rpc_version):
        """Prepare fanout context, one topic per object type."""
        obj_topic = resource_type_versioned_topic(obj.obj_name(),
                                                  resource_version)
        return self.client.prepare(fanout=True, topic=obj_topic,
                                   version=rpc_version)

    @staticmethod
    def _classify_resources_by_type(resource_list):
        resources_by_type = collections.defaultdict(list)
        for resource in resource_list:
            resource_type = resources.get_resource_type(resource)
            resources_by_type[resource_type].append(resource)
        return resources_by_type

    @log_helpers.log_method_call
    def push(self, context, resource_list, event_type):
        """Push an event and list of resources to agents, batched per type.
        When a list of different resource types is passed to this method,
        the push will be sent as separate individual list pushes, one per
        resource type.
        """

        resources_by_type = self._classify_resources_by_type(resource_list)
        for resource_type, type_resources in resources_by_type.items():
            self._push(context, resource_type, type_resources, event_type)

    def _push(self, context, resource_type, resource_list, event_type):
        """Push an event and list of resources of the same type to agents."""
        _validate_resource_type(resource_type)
        compat_call = len(resource_list) == 1

        for version in version_manager.get_resource_versions(resource_type):
            cctxt = self._prepare_object_fanout_context(
                resource_list[0], version,
                rpc_version='1.0' if compat_call else '1.1')

            dehydrated_resources = [
                resource.obj_to_primitive(target_version=version)
                for resource in resource_list]

            if compat_call:
                #TODO(mangelajo): remove in Ocata, backwards compatibility
                #                 for agents expecting a single element as
                #                 a single element instead of a list, this
                #                 is only relevant to the QoSPolicy topic queue
                cctxt.cast(context, 'push',
                           resource=dehydrated_resources[0],
                           event_type=event_type)
            else:
                cctxt.cast(context, 'push',
                           resource_list=dehydrated_resources,
                           event_type=event_type)


class ResourcesPushRpcCallback(object):
    """Agent-side RPC for plugin-to-agents interaction.

    This class implements the receiver for notification about versioned objects
    resource updates used by neutron.api.rpc.callbacks. You can find the
    caller side in ResourcesPushRpcApi.
    """
    # History
    #   1.0 Initial version
    #   1.1 push method introduces resource_list support

    target = oslo_messaging.Target(version='1.1',
                                   namespace=constants.RPC_NAMESPACE_RESOURCES)

    def push(self, context, **kwargs):
        """Push receiver, will always receive resources of the same type."""
        # TODO(mangelajo): accept single 'resource' parameter for backwards
        #                  compatibility during Newton, remove in Ocata
        resource_list = ([kwargs['resource']] if 'resource' in kwargs else
                         kwargs['resource_list'])
        event_type = kwargs['event_type']

        resource_objs = [
            obj_base.NeutronObject.clean_obj_from_primitive(resource)
            for resource in resource_list]

        resource_type = resources.get_resource_type(resource_objs[0])
        cons_registry.push(resource_type, resource_objs, event_type)
