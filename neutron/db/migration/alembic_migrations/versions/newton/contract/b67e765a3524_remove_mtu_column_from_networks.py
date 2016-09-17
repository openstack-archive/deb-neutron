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

"""Remove mtu column from networks.

Revision ID: b67e765a3524
Revises: 4bcd4df1f426
Create Date: 2016-07-17 02:07:36.625196

"""

# revision identifiers, used by Alembic.
revision = 'b67e765a3524'
down_revision = '4bcd4df1f426'

from alembic import op


def upgrade():
    op.drop_column('networks', 'mtu')
