from collections import defaultdict, namedtuple
from typing import List

from django.db.models.aggregates import Count
from django.utils.functional import cached_property
from django.utils.translation import ugettext as _

from dateutil.rrule import DAILY, FR, MO, SA, TH, TU, WE, rrule
from sqlagg.columns import SimpleColumn
from sqlagg.filters import EQ
from sqlagg.sorting import OrderBy

from corehq.apps.fixtures.dbaccessors import (
    get_fixture_data_types_in_domain,
    get_fixture_items_for_data_type,
)
from corehq.apps.fixtures.models import FixtureDataItem
from corehq.apps.reports.datatables import DataTablesColumn, DataTablesHeader
from corehq.apps.reports.filters.dates import DatespanFilter
from corehq.apps.reports.generic import GenericTabularReport
from corehq.apps.reports.sqlreport import DatabaseColumn, SqlData
from corehq.apps.reports.standard import CustomProjectReport, DatespanMixin
from corehq.apps.sms.models import (
    INCOMING,
    OUTGOING,
    SMS,
    MessagingEvent,
    MessagingSubEvent,
)
from corehq.apps.userreports.util import get_table_name
from corehq.sql_db.connections import DEFAULT_ENGINE_ID
from custom.abt.reports.filters import (
    CountryFilter,
    LevelFourFilter,
    LevelOneFilter,
    LevelThreeFilter,
    LevelTwoFilter,
    SubmissionStatusFilter,
    UsernameFilter,
)

LocationTuple = namedtuple('LocationTuple', [
    'id',
    'name',
    'country',
    'level_1_name',
    'level_2_name',
    'level_3_name',
    'level_4_name',
])


class LatePMTUsers(SqlData):
    engine_id = DEFAULT_ENGINE_ID

    @property
    def table_name(self):
        return get_table_name(self.domain, "static-late-pmt")

    @property
    def domain(self):
        return self.config['domain']

    @property
    def filters(self):
        filters = []
        filter_fields = [
            'country',
            'level_1',
            'level_2',
            'level_3',
            'level_4',
        ]
        for filter_field in filter_fields:
            if filter_field in self.config and self.config[filter_field]:
                filters.append(EQ(filter_field, filter_field))
        if 'user_id' in self.config and self.config['user_id']:
            filters.append(EQ('doc_id', 'user_id'))
        return filters

    @property
    def group_by(self):
        return [
            'user_id',
            'username',
            'phone_number',
            'country',
            'level_1',
            'level_2',
            'level_3',
            'level_4',
        ]

    @property
    def order_by(self):
        return [OrderBy('username')]

    @property
    def columns(self):
        return [
            DatabaseColumn('user_id', SimpleColumn('doc_id', alias='user_id')),
            DatabaseColumn('username', SimpleColumn('username')),
            DatabaseColumn('phone_number', SimpleColumn('phone_number')),
            DatabaseColumn('country', SimpleColumn('country')),
            DatabaseColumn('level_1', SimpleColumn('level_1')),
            DatabaseColumn('level_2', SimpleColumn('level_2')),
            DatabaseColumn('level_3', SimpleColumn('level_3')),
            DatabaseColumn('level_4', SimpleColumn('level_4'))
        ]


class LatePmtReport(GenericTabularReport, CustomProjectReport, DatespanMixin):
    report_title = "Late PMT"
    slug = 'late_pmt'
    name = "Late PMT"

    languages = (
        'en',
        'fra',
        'por'
    )

    fields = [
        DatespanFilter,
        UsernameFilter,
        CountryFilter,
        LevelOneFilter,
        LevelTwoFilter,
        LevelThreeFilter,
        LevelFourFilter,
        SubmissionStatusFilter,
    ]

    @property
    def report_config(self):
        return {
            'domain': self.domain,
            'startdate': self.startdate,
            'enddate': self.enddate,
            'user_id': self.request.GET.get('user_id', ''),
            'country': self.request.GET.get('country', ''),
            'level_1': self.request.GET.get('level_1', ''),
            'level_2': self.request.GET.get('level_2', ''),
            'level_3': self.request.GET.get('level_3', ''),
            'level_4': self.request.GET.get('level_4', ''),
            'submission_status': self.request.GET.get('submission_status', '')
        }

    @property
    def startdate(self):
        return self.request.datespan.startdate

    @property
    def enddate(self):
        return self.request.datespan.end_of_end_day

    @property
    def headers(self):
        return DataTablesHeader(
            DataTablesColumn(_("Missing Report Date")),
            DataTablesColumn(_("Username")),
            DataTablesColumn(_("Phone Number")),
            DataTablesColumn(_("Country")),
            DataTablesColumn(_("Level 1")),
            DataTablesColumn(_("Level 2")),
            DataTablesColumn(_("Level 3")),
            DataTablesColumn(_("Level 4")),
            DataTablesColumn(_("Submission Status")),
        )

    @cached_property
    def smss_received(self):
        data = SMS.objects.filter(
            domain=self.domain,
            couch_recipient_doc_type='CommCareUser',
            direction=INCOMING,
            couch_recipient__in=self.get_user_ids,
            date__range=(
                self.startdate,
                self.enddate
            )
        ).exclude(
            text="123"
        ).values('date', 'couch_recipient').annotate(
            number_of_sms=Count('couch_recipient')
        )
        return {(sms['date'].date(), sms['couch_recipient']) for sms in data}

    @cached_property
    def valid_smss_received(self):
        data = MessagingSubEvent.objects.filter(
            parent__domain=self.domain,
            parent__recipient_type=MessagingEvent.RECIPIENT_MOBILE_WORKER,
            parent__source=MessagingEvent.SOURCE_KEYWORD,
            xforms_session__isnull=False,
            xforms_session__submission_id__isnull=False,
            recipient_id__in=self.get_user_ids,
            date__range=(
                self.startdate,
                self.enddate
            )
        ).values('date', 'recipient_id')
        return {(subevent['date'].date(), subevent['recipient_id']) for subevent in data}

    @cached_property
    def get_user_ids(self):
        return [user['user_id'] for user in self.get_users]

    @cached_property
    def get_users(self):
        return LatePMTUsers(config=self.report_config).get_data()

    @property
    def rows(self):
        def _to_report_format(date, user, error_msg):
            return [
                date.strftime("%Y-%m-%d"),
                user['username'].split('@')[0],
                user['phone_number'],
                user['country'],
                user['level_1'],
                user['level_2'],
                user['level_3'],
                user['level_4'],
                error_msg
            ]

        users = self.get_users
        dates = rrule(
            DAILY,
            dtstart=self.startdate,
            until=self.enddate,
            byweekday=(MO, TU, WE, TH, FR, SA)
        )
        include_missing_pmt_data = self.report_config['submission_status'] != 'group_b'
        include_incorrect_pmt_data = self.report_config['submission_status'] != 'group_a'
        rows = []
        if users:
            for date in dates:
                for user in users:
                    sms_received = (date.date(), user['user_id']) in self.smss_received
                    valid_sms = (date.date(), user['user_id']) in self.valid_smss_received
                    if not sms_received and include_missing_pmt_data:
                        error_msg = _('No PMT data Submitted')
                    elif sms_received and not valid_sms and include_incorrect_pmt_data:
                        error_msg = _('Incorrect PMT data Submitted')
                    else:
                        continue
                    rows.append(_to_report_format(date, user, error_msg))
        return rows

    @cached_property
    def locations(self) -> List[LocationTuple]:
        data_types_by_tag = {
            dt.tag: dt
            for dt in get_fixture_data_types_in_domain(self.domain)
        }
        level_1_items = get_fixture_items_for_data_type(
            self.domain,
            data_types_by_tag["level_1_dcv"]._id
        )
        level_1_dicts = [fixture_data_item_to_dict(di) for di in level_1_items]
        level_2s_by_level_1 = get_fixture_dicts_by_key(
            self.domain,
            data_type_id=data_types_by_tag["level_2_dcv"]._id,
            key='level_1_dcv'
        )
        level_3s_by_level_2 = get_fixture_dicts_by_key(
            self.domain,
            data_type_id=data_types_by_tag["level_3_dcv"]._id,
            key='level_2_dcv'
        )
        level_4s_by_level_3 = get_fixture_dicts_by_key(
            self.domain,
            data_type_id=data_types_by_tag["level_4_dcv"]._id,
            key='level_3_dcv'
        )
        country_has_level_4 = len(level_4s_by_level_3) > 1

        locations = []
        for level_1 in level_1_dicts:
            for level_2 in level_2s_by_level_1[level_1['id']]:
                for level_3 in level_3s_by_level_2[level_2['id']]:
                    if country_has_level_4:
                        for level_4 in level_4s_by_level_3[level_3['id']]:
                            locations.append(LocationTuple(
                                id=level_4['id'],
                                name=level_4['name'],
                                country=level_1['country'],
                                level_1_name=level_1['name'],
                                level_2_name=level_2['name'],
                                level_3_name=level_3['name'],
                                level_4_name=level_4['name'],
                            ))
                    else:
                        locations.append(LocationTuple(
                            id=level_3['id'],
                            name=level_3['name'],
                            country=level_1['country'],
                            level_1_name=level_1['name'],
                            level_2_name=level_2['name'],
                            level_3_name=level_3['name'],
                            level_4_name=None,
                        ))
        return locations


def get_fixture_dicts_by_key(
    domain: str,
    data_type_id: str,
    key: str,
) -> dict:
    dicts_by_key = defaultdict(list)
    for data_item in get_fixture_items_for_data_type(domain, data_type_id):
        dict_ = fixture_data_item_to_dict(data_item)
        dicts_by_key[dict_[key]].append(dict_)
    return dicts_by_key


def fixture_data_item_to_dict(
    data_item: FixtureDataItem,
) -> dict:
    """
    Transforms a FixtureDataItem to a dict.

    A ``FixtureDataItem.fields`` value looks like this::

        {
            'id': FieldList(
                doc_type='FieldList',
                field_list=[
                    FixtureItemField(
                        doc_type='FixtureItemField',
                        field_value='migori_county',
                        properties={}
                    )
                ]
            ),
            'name': FieldList(
                doc_type='FieldList',
                field_list=[
                    FixtureItemField(
                        doc_type='FixtureItemField',
                        field_value='Migori',
                        properties={'lang': 'en'}
                    )
                ]
            ),
            # ... etc. ...
        }

    Only the first value in each ``FieldList`` is selected.

    .. WARNING:: THIS MEANS THAT TRANSLATIONS ARE NOT SUPPORTED.

    The return value for the example above would be::

        {
            'id': 'migori_county',
            'name': 'Migori'
        }

    """
    return {
        key: field_list.field_list[0].field_value
        for key, field_list in data_item.fields.items()
    }
