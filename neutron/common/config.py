# Copyright 2011 VMware, Inc.
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

"""
Routines for configuring Neutron
"""

import sys

from keystoneauth1 import loading as ks_loading
from neutron_lib.api import validators
from oslo_config import cfg
from oslo_db import options as db_options
from oslo_log import log as logging
import oslo_messaging
from oslo_middleware import cors
from oslo_service import wsgi

from neutron._i18n import _, _LI
from neutron.conf import common as common_config
from neutron import policy
from neutron import version


LOG = logging.getLogger(__name__)

# Register the configuration options
common_config.register_core_common_config_opts()

# Ensure that the control exchange is set correctly
oslo_messaging.set_transport_defaults(control_exchange='neutron')


def set_db_defaults():
    # Update the default QueuePool parameters. These can be tweaked by the
    # conf variables - max_pool_size, max_overflow and pool_timeout
    db_options.set_defaults(
        cfg.CONF,
        connection='sqlite://',
        max_pool_size=10,
        max_overflow=20, pool_timeout=10)

set_db_defaults()

NOVA_CONF_SECTION = 'nova'

ks_loading.register_auth_conf_options(cfg.CONF, NOVA_CONF_SECTION)
ks_loading.register_session_conf_options(cfg.CONF, NOVA_CONF_SECTION)


# Register the nova configuration options
common_config.register_nova_opts()

logging.register_options(cfg.CONF)


def init(args, **kwargs):
    cfg.CONF(args=args, project='neutron',
             version='%%(prog)s %s' % version.version_info.release_string(),
             **kwargs)

    # FIXME(ihrachys): if import is put in global, circular import
    # failure occurs
    from neutron.common import rpc as n_rpc
    n_rpc.init(cfg.CONF)

    # Validate that the base_mac is of the correct format
    msg = validators.validate_regex(cfg.CONF.base_mac, validators.MAC_PATTERN)
    if msg:
        msg = _("Base MAC: %s") % msg
        raise Exception(msg)


def setup_logging():
    """Sets up the logging options for a log with supplied name."""
    product_name = "neutron"
    logging.setup(cfg.CONF, product_name)
    # We use the oslo.log default log levels and add only the extra levels
    # that Neutron needs.
    logging.set_defaults(default_log_levels=logging.get_default_log_levels())
    LOG.info(_LI("Logging enabled!"))
    LOG.info(_LI("%(prog)s version %(version)s"),
             {'prog': sys.argv[0],
              'version': version.version_info.release_string()})
    LOG.debug("command line: %s", " ".join(sys.argv))


def reset_service():
    # Reset worker in case SIGHUP is called.
    # Note that this is called only in case a service is running in
    # daemon mode.
    setup_logging()
    set_config_defaults()
    policy.refresh()


def load_paste_app(app_name):
    """Builds and returns a WSGI app from a paste config file.

    :param app_name: Name of the application to load
    """
    loader = wsgi.Loader(cfg.CONF)
    app = loader.load_app(app_name)
    return app


def set_config_defaults():
    """This method updates all configuration default values."""
    set_cors_middleware_defaults()


def set_cors_middleware_defaults():
    """Update default configuration options for oslo.middleware."""
    # CORS Defaults
    # TODO(krotscheck): Update with https://review.openstack.org/#/c/285368/
    cfg.set_defaults(cors.CORS_OPTS,
                     allow_headers=['X-Auth-Token',
                                    'X-Identity-Status',
                                    'X-Roles',
                                    'X-Service-Catalog',
                                    'X-User-Id',
                                    'X-Tenant-Id',
                                    'X-OpenStack-Request-ID'],
                     expose_headers=['X-Auth-Token',
                                     'X-Subject-Token',
                                     'X-Service-Token',
                                     'X-OpenStack-Request-ID',
                                     'OpenStack-Volume-microversion'],
                     allow_methods=['GET',
                                    'PUT',
                                    'POST',
                                    'DELETE',
                                    'PATCH']
                     )
