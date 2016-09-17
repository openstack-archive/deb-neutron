# Copyright 2015 Mirantis Inc.
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

import mock

from oslo_config import cfg

from neutron import service
from neutron.tests import base
from neutron.tests.unit import test_wsgi


class TestRpcWorker(test_wsgi.TestServiceBase):

    def test_reset(self):
        _plugin = mock.Mock()
        rpc_worker = service.RpcWorker(_plugin)
        self._test_reset(rpc_worker)


class TestRunWsgiApp(base.BaseTestCase):
    def setUp(self):
        super(TestRunWsgiApp, self).setUp()
        self.processor_count = mock.patch(
            'oslo_concurrency.processutils.get_worker_count'
        ).start().return_value

    def _test_api_workers(self, config_value, expected_passed_value):
        if config_value is not None:
            cfg.CONF.set_override('api_workers', config_value)
        with mock.patch('neutron.wsgi.Server') as mock_server:
            service.run_wsgi_app(mock.sentinel.app)
        start_call = mock_server.return_value.start.call_args
        expected_call = mock.call(
            mock.ANY, mock.ANY, mock.ANY, workers=expected_passed_value)
        self.assertEqual(expected_call, start_call)

    def test_api_workers_zero(self):
        self._test_api_workers(0, 0)

    def test_api_workers_default(self):
        self._test_api_workers(None, self.processor_count)

    def test_api_workers_defined(self):
        self._test_api_workers(42, 42)
