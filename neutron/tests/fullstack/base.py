# Copyright 2015 Red Hat, Inc.
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

import os

from oslo_config import cfg

from neutron.tests import base as tests_base
from neutron.tests.common import helpers
from neutron.tests.fullstack.resources import client as client_resource
from neutron.tests import tools
from neutron.tests.unit import testlib_api


# This is the directory from which infra fetches log files for fullstack tests
DEFAULT_LOG_DIR = os.path.join(helpers.get_test_log_path(),
                               'dsvm-fullstack-logs')


class BaseFullStackTestCase(testlib_api.MySQLTestCaseMixin,
                            testlib_api.SqlTestCase):
    """Base test class for full-stack tests."""

    BUILD_WITH_MIGRATIONS = True

    def setUp(self, environment):
        super(BaseFullStackTestCase, self).setUp()

        tests_base.setup_test_logging(
            cfg.CONF, DEFAULT_LOG_DIR, '%s.txt' % self.get_name())

        # NOTE(zzzeek): the opportunistic DB fixtures have built for
        # us a per-test (or per-process) database.  Set the URL of this
        # database in CONF as the full stack tests need to actually run a
        # neutron server against this database.
        _orig_db_url = cfg.CONF.database.connection
        cfg.CONF.set_override(
            'connection', str(self.engine.url), group='database')
        self.addCleanup(
            cfg.CONF.set_override,
            "connection", _orig_db_url, group="database"
        )

        # NOTE(ihrachys): seed should be reset before environment fixture below
        # since the latter starts services that may rely on generated port
        # numbers
        tools.reset_random_seed()
        self.environment = environment
        self.environment.test_name = self.get_name()
        self.useFixture(self.environment)
        self.client = self.environment.neutron_server.client
        self.safe_client = self.useFixture(
            client_resource.ClientFixture(self.client))

    def get_name(self):
        class_name, test_name = self.id().split(".")[-2:]
        return "%s.%s" % (class_name, test_name)
