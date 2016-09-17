# All rights reserved.
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
#

import netaddr

from neutron import context as nctx
from neutron.db import models_v2
from neutron import manager
from neutron.tests.unit.plugins.ml2 import test_plugin


class TestRevisionPlugin(test_plugin.Ml2PluginV2TestCase):

    def get_additional_service_plugins(self):
        p = super(TestRevisionPlugin, self).get_additional_service_plugins()
        p.update({'revision_plugin_name': 'revisions'})
        return p

    def setUp(self):
        super(TestRevisionPlugin, self).setUp()
        self.cp = manager.NeutronManager.get_plugin()
        self.l3p = (manager.NeutronManager.
                    get_service_plugins()['L3_ROUTER_NAT'])
        self.ctx = nctx.get_admin_context()

    def test_handle_expired_object(self):
        rp = manager.NeutronManager.get_service_plugins()['revision_plugin']
        with self.port():
            with self.ctx.session.begin():
                ipal_obj = self.ctx.session.query(models_v2.IPAllocation).one()
                # load port into our session
                port_obj = self.ctx.session.query(models_v2.Port).one()
                # simulate concurrent delete in another session
                nctx.get_admin_context().session.query(models_v2.Port).delete()
                # expire the port so the revision bumping code will trigger a
                # lookup on its attributes and encounter an ObjectDeletedError
                self.ctx.session.expire(port_obj)
                rp._bump_related_revisions(self.ctx.session, ipal_obj)

    def test_port_name_update_revises(self):
        with self.port() as port:
            rev = port['port']['revision']
            new = {'port': {'name': 'seaweed'}}
            response = self._update('ports', port['port']['id'], new)
            new_rev = response['port']['revision']
            self.assertGreater(new_rev, rev)

    def test_port_ip_update_revises(self):
        with self.port() as port:
            rev = port['port']['revision']
            new = {'port': {'fixed_ips': port['port']['fixed_ips']}}
            # ensure adding an IP allocation updates the port
            next_ip = str(netaddr.IPAddress(
                  new['port']['fixed_ips'][0]['ip_address']) + 1)
            new['port']['fixed_ips'].append({'ip_address': next_ip})
            response = self._update('ports', port['port']['id'], new)
            self.assertEqual(2, len(response['port']['fixed_ips']))
            new_rev = response['port']['revision']
            self.assertGreater(new_rev, rev)
            # ensure deleting an IP allocation updates the port
            rev = new_rev
            new['port']['fixed_ips'].pop()
            response = self._update('ports', port['port']['id'], new)
            self.assertEqual(1, len(response['port']['fixed_ips']))
            new_rev = response['port']['revision']
            self.assertGreater(new_rev, rev)

    def test_security_group_rule_ops_bump_security_group(self):
        s = {'security_group': {'tenant_id': 'some_tenant', 'name': '',
                                'description': 's'}}
        sg = self.cp.create_security_group(self.ctx, s)
        s['security_group']['name'] = 'hello'
        updated = self.cp.update_security_group(self.ctx, sg['id'], s)
        self.assertGreater(updated['revision'], sg['revision'])
        # ensure rule changes bump parent SG
        r = {'security_group_rule': {'tenant_id': 'some_tenant',
                                     'port_range_min': 80, 'protocol': 6,
                                     'port_range_max': 90,
                                     'remote_ip_prefix': '0.0.0.0/0',
                                     'ethertype': 'IPv4',
                                     'remote_group_id': None,
                                     'direction': 'ingress',
                                     'security_group_id': sg['id']}}
        rule = self.cp.create_security_group_rule(self.ctx, r)
        sg = updated
        updated = self.cp.get_security_group(self.ctx, sg['id'])
        self.assertGreater(updated['revision'], sg['revision'])
        self.cp.delete_security_group_rule(self.ctx, rule['id'])
        sg = updated
        updated = self.cp.get_security_group(self.ctx, sg['id'])
        self.assertGreater(updated['revision'], sg['revision'])

    def test_router_interface_ops_bump_router(self):
        r = {'router': {'name': 'myrouter', 'tenant_id': 'some_tenant',
                        'admin_state_up': True}}
        router = self.l3p.create_router(self.ctx, r)
        r['router']['name'] = 'yourrouter'
        updated = self.l3p.update_router(self.ctx, router['id'], r)
        self.assertGreater(updated['revision'], router['revision'])
        # add an intf and make sure it bumps rev
        with self.subnet(tenant_id='some_tenant') as s:
            interface_info = {'subnet_id': s['subnet']['id']}
        self.l3p.add_router_interface(self.ctx, router['id'], interface_info)
        router = updated
        updated = self.l3p.get_router(self.ctx, router['id'])
        self.assertGreater(updated['revision'], router['revision'])
        self.l3p.remove_router_interface(self.ctx, router['id'],
                                         interface_info)
        router = updated
        updated = self.l3p.get_router(self.ctx, router['id'])
        self.assertGreater(updated['revision'], router['revision'])
