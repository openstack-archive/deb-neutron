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

import abc
import collections

from neutron_lib import exceptions
from oslo_concurrency import lockutils
from oslo_log import log as logging
import six

from neutron._i18n import _LW, _LI
from neutron.agent.l2 import l2_agent_extension
from neutron.agent.linux import tc_lib
from neutron.api.rpc.callbacks.consumer import registry
from neutron.api.rpc.callbacks import events
from neutron.api.rpc.callbacks import resources
from neutron.api.rpc.handlers import resources_rpc
from neutron import manager

LOG = logging.getLogger(__name__)


@six.add_metaclass(abc.ABCMeta)
class QosAgentDriver(object):
    """Defines stable abstract interface for QoS Agent Driver.

    QoS Agent driver defines the interface to be implemented by Agent
    for applying QoS Rules on a port.
    """

    # Each QoS driver should define the set of rule types that it supports, and
    # corresponding handlers that has the following names:
    #
    # create_<type>
    # update_<type>
    # delete_<type>
    #
    # where <type> is one of VALID_RULE_TYPES
    SUPPORTED_RULES = set()

    @abc.abstractmethod
    def initialize(self):
        """Perform QoS agent driver initialization.
        """

    def create(self, port, qos_policy):
        """Apply QoS rules on port for the first time.

        :param port: port object.
        :param qos_policy: the QoS policy to be applied on port.
        """
        self._handle_update_create_rules('create', port, qos_policy)

    def consume_api(self, agent_api):
        """Consume the AgentAPI instance from the QoSAgentExtension class

        This allows QosAgentDrivers to gain access to resources limited to the
        NeutronAgent when this method is overridden.

        :param agent_api: An instance of an agent specific API
        """

    def update(self, port, qos_policy):
        """Apply QoS rules on port.

        :param port: port object.
        :param qos_policy: the QoS policy to be applied on port.
        """
        self._handle_update_create_rules('update', port, qos_policy)

    def delete(self, port, qos_policy=None):
        """Remove QoS rules from port.

        :param port: port object.
        :param qos_policy: the QoS policy to be removed from port.
        """
        if qos_policy is None:
            rule_types = self.SUPPORTED_RULES
        else:
            rule_types = set(
                [rule.rule_type
                 for rule in self._iterate_rules(qos_policy.rules)])

        for rule_type in rule_types:
            self._handle_rule_delete(port, rule_type)

    def _iterate_rules(self, rules):
        for rule in rules:
            rule_type = rule.rule_type
            if rule_type in self.SUPPORTED_RULES:
                yield rule
            else:
                LOG.warning(_LW('Unsupported QoS rule type for %(rule_id)s: '
                                '%(rule_type)s; skipping'),
                            {'rule_id': rule.id, 'rule_type': rule_type})

    def _handle_rule_delete(self, port, rule_type):
        handler_name = "".join(("delete_", rule_type))
        handler = getattr(self, handler_name)
        handler(port)

    def _handle_update_create_rules(self, action, port, qos_policy):
        for rule in self._iterate_rules(qos_policy.rules):
            if rule.should_apply_to_port(port):
                handler_name = "".join((action, "_", rule.rule_type))
                handler = getattr(self, handler_name)
                handler(port, rule)
            else:
                LOG.debug("Port %(port)s excluded from QoS rule %(rule)s",
                          {'port': port, 'rule': rule.id})

    def _get_egress_burst_value(self, rule):
        """Return burst value used for egress bandwidth limitation.

        Because Egress bw_limit is done on ingress qdisc by LB and ovs drivers
        so it will return burst_value used by tc on as ingress_qdisc.
        """
        return tc_lib.TcCommand.get_ingress_qdisc_burst_value(
                rule.max_kbps, rule.max_burst_kbps)


class PortPolicyMap(object):
    def __init__(self):
        # we cannot use a dict of sets here because port dicts are not hashable
        self.qos_policy_ports = collections.defaultdict(dict)
        self.known_policies = {}
        self.port_policies = {}

    def get_ports(self, policy):
        return self.qos_policy_ports[policy.id].values()

    def get_policy(self, policy_id):
        return self.known_policies.get(policy_id)

    def update_policy(self, policy):
        self.known_policies[policy.id] = policy

    def has_policy_changed(self, port, policy_id):
        return self.port_policies.get(port['port_id']) != policy_id

    def get_port_policy(self, port):
        policy_id = self.port_policies.get(port['port_id'])
        if policy_id:
            return self.get_policy(policy_id)

    def set_port_policy(self, port, policy):
        """Attach a port to policy and return any previous policy on port."""
        port_id = port['port_id']
        old_policy = self.get_port_policy(port)
        self.known_policies[policy.id] = policy
        self.port_policies[port_id] = policy.id
        self.qos_policy_ports[policy.id][port_id] = port
        if old_policy and old_policy.id != policy.id:
            del self.qos_policy_ports[old_policy.id][port_id]
        return old_policy

    def clean_by_port(self, port):
        """Detach port from policy and cleanup data we don't need anymore."""
        port_id = port['port_id']
        if port_id in self.port_policies:
            del self.port_policies[port_id]
            for qos_policy_id, port_dict in self.qos_policy_ports.items():
                if port_id in port_dict:
                    del port_dict[port_id]
                    if not port_dict:
                        self._clean_policy_info(qos_policy_id)
                    return
        raise exceptions.PortNotFound(port_id=port['port_id'])

    def _clean_policy_info(self, qos_policy_id):
        del self.qos_policy_ports[qos_policy_id]
        del self.known_policies[qos_policy_id]


class QosAgentExtension(l2_agent_extension.L2AgentExtension):
    SUPPORTED_RESOURCE_TYPES = [resources.QOS_POLICY]

    def initialize(self, connection, driver_type):
        """Initialize agent extension."""

        self.resource_rpc = resources_rpc.ResourcesPullRpcApi()
        self.qos_driver = manager.NeutronManager.load_class_for_provider(
            'neutron.qos.agent_drivers', driver_type)()
        self.qos_driver.consume_api(self.agent_api)
        self.qos_driver.initialize()

        self.policy_map = PortPolicyMap()

        self._register_rpc_consumers(connection)

    def consume_api(self, agent_api):
        """Allows an extension to gain access to resources internal to the
           neutron agent and otherwise unavailable to the extension.
        """
        self.agent_api = agent_api

    def _register_rpc_consumers(self, connection):
        """Allows an extension to receive notifications of updates made to
           items of interest.
        """
        endpoints = [resources_rpc.ResourcesPushRpcCallback()]
        for resource_type in self.SUPPORTED_RESOURCE_TYPES:
            # We assume that the neutron server always broadcasts the latest
            # version known to the agent
            registry.subscribe(self._handle_notification, resource_type)
            topic = resources_rpc.resource_type_versioned_topic(resource_type)
            connection.create_consumer(topic, endpoints, fanout=True)

    @lockutils.synchronized('qos-port')
    def _handle_notification(self, qos_policies, event_type):
        # server does not allow to remove a policy that is attached to any
        # port, so we ignore DELETED events. Also, if we receive a CREATED
        # event for a policy, it means that there are no ports so far that are
        # attached to it. That's why we are interested in UPDATED events only
        if event_type == events.UPDATED:
            for qos_policy in qos_policies:
                self._process_update_policy(qos_policy)

    @lockutils.synchronized('qos-port')
    def handle_port(self, context, port):
        """Handle agent QoS extension for port.

        This method applies a new policy to a port using the QoS driver.
        Update events are handled in _handle_notification.
        """
        port_id = port['port_id']
        port_qos_policy_id = port.get('qos_policy_id')
        network_qos_policy_id = port.get('network_qos_policy_id')
        qos_policy_id = port_qos_policy_id or network_qos_policy_id
        if qos_policy_id is None:
            self._process_reset_port(port)
            return

        if not self.policy_map.has_policy_changed(port, qos_policy_id):
            return

        qos_policy = self.resource_rpc.pull(
            context, resources.QOS_POLICY, qos_policy_id)
        if qos_policy is None:
            LOG.info(_LI("QoS policy %(qos_policy_id)s applied to port "
                         "%(port_id)s is not available on server, "
                         "it has been deleted. Skipping."),
                     {'qos_policy_id': qos_policy_id, 'port_id': port_id})
            self._process_reset_port(port)
        else:
            old_qos_policy = self.policy_map.set_port_policy(port, qos_policy)
            if old_qos_policy:
                self.qos_driver.delete(port, old_qos_policy)
                self.qos_driver.update(port, qos_policy)
            else:
                self.qos_driver.create(port, qos_policy)

    def delete_port(self, context, port):
        self._process_reset_port(port)

    def _policy_rules_modified(self, old_policy, policy):
        return not (len(old_policy.rules) == len(policy.rules) and
                    all(i in old_policy.rules for i in policy.rules))

    def _process_update_policy(self, qos_policy):
        old_qos_policy = self.policy_map.get_policy(qos_policy.id)
        if old_qos_policy:
            if self._policy_rules_modified(old_qos_policy, qos_policy):
                for port in self.policy_map.get_ports(qos_policy):
                    #NOTE(QoS): for now, just reflush the rules on the port.
                    #           Later, we may want to apply the difference
                    #           between the old and new rule lists.
                    self.qos_driver.delete(port, old_qos_policy)
                    self.qos_driver.update(port, qos_policy)
            self.policy_map.update_policy(qos_policy)

    def _process_reset_port(self, port):
        try:
            self.policy_map.clean_by_port(port)
            self.qos_driver.delete(port)
        except exceptions.PortNotFound:
            LOG.info(_LI("QoS extension did have no information about the "
                         "port %s that we were trying to reset"),
                     port['port_id'])
