import math
import time
import traceback

from django.conf import settings

from corehq.util.es.elasticsearch import (
    ConflictError,
    ConnectionError,
    NotFoundError,
    RequestError,
)
from corehq.util.es.interface import ElasticsearchInterface
from corehq.util.metrics import metrics_histogram_timer
from corehq.util.quickcache import quickcache

from pillowtop.exceptions import BulkDocException, PillowtopIndexingError
from pillowtop.logger import pillow_logging
from pillowtop.utils import (
    ErrorCollector,
    build_bulk_payload,
    bulk_fetch_changes_docs,
    ensure_document_exists,
    ensure_matched_revisions,
    get_errors_with_ids,
)

from .interface import BulkPillowProcessor, PillowProcessor


def identity(x):
    return x


def noop_filter(x):
    return False


RETRY_INTERVAL = 2  # seconds, exponentially increasing
MAX_RETRIES = 4  # exponential factor threshold for alerts


class ElasticProcessor(PillowProcessor):
    """Generic processor to transform documents and insert into ES.

    Processes one document at a time.

    Reads from:
      - Usually Couch
      - Sometimes SQL

    Writes to:
      - ES
    """

    def __init__(self, elasticsearch, index_info, doc_prep_fn=None, doc_filter_fn=None,
            force_lookup=False):
        self.doc_filter_fn = doc_filter_fn or noop_filter
        self.elasticsearch = elasticsearch
        self.es_interface = ElasticsearchInterface(self.elasticsearch)
        self.index_info = index_info
        self.doc_transform_fn = doc_prep_fn or identity
        self.force_lookup = force_lookup

    def es_getter(self):
        return self.elasticsearch

    def process_change(self, change):
        if change.deleted and change.id:
            self._delete_doc_if_exists(change.id)
            return

        with self._datadog_timing('extract'):
            doc = change.get_document()

            ensure_document_exists(change)
            ensure_matched_revisions(change, doc)

        with self._datadog_timing('transform'):
            if doc is None or (self.doc_filter_fn and self.doc_filter_fn(doc)):
                return

            if doc.get('doc_type') is not None and doc['doc_type'].endswith("-Deleted"):
                self._delete_doc_if_exists(change.id)
                return

            # prepare doc for es
            doc_ready_to_save = self.doc_transform_fn(doc)

        # send it across
        with self._datadog_timing('load'):
            send_to_elasticsearch(
                index_info=self.index_info,
                doc_type=self.index_info.type,
                doc_id=change.id,
                es_getter=self.es_getter,
                name='ElasticProcessor',
                data=doc_ready_to_save,
                # Todo; tweak based on whether its a fresh form
                force_lookup=self.force_lookup,
            )

    def _delete_doc_if_exists(self, doc_id):
        send_to_elasticsearch(
            index_info=self.index_info,
            doc_type=self.index_info.type,
            doc_id=doc_id,
            es_getter=self.es_getter,
            name='ElasticProcessor',
            delete=True
        )

    def _datadog_timing(self, step):
        return metrics_histogram_timer(
            'commcare.change_feed.processor.timing',
            timing_buckets=(.03, .1, .3, 1, 3, 10),
            tags={
                'action': step,
                'index': self.index_info.alias,
            })


class BulkElasticProcessor(ElasticProcessor, BulkPillowProcessor):
    """Generic processor to transform documents and insert into ES.

    Processes one "chunk" of changes at a time (chunk size specified by pillow).

    Reads from:
      - Usually Couch
      - Sometimes SQL

    Writes to:
      - ES
    """

    def process_changes_chunk(self, changes_chunk):
        with self._datadog_timing('bulk_extract'):
            bad_changes, docs = bulk_fetch_changes_docs(changes_chunk)

        with self._datadog_timing('bulk_transform'):
            changes_to_process = {
                change.id: change
                for change in changes_chunk
                if change.document and not self.doc_filter_fn(change.document)
            }
            retry_changes = list(bad_changes)

            error_collector = ErrorCollector()
            es_actions = build_bulk_payload(
                self.index_info, list(changes_to_process.values()), self.doc_transform_fn, error_collector,
                force_lookup=self.force_lookup
            )
            error_changes = error_collector.errors

        try:
            with self._datadog_timing('bulk_load'):
                _, errors = self.es_interface.bulk_ops(
                    es_actions, raise_on_error=False, raise_on_exception=False)
        except Exception as e:
            pillow_logging.exception("[%s] ES bulk load error")
            error_changes.extend([
                (change, e) for change in changes_to_process.values()
            ])
        else:
            for change_id, error_msg in get_errors_with_ids(errors):
                error_changes.append((changes_to_process[change_id], BulkDocException(error_msg)))
        return retry_changes, error_changes


@quickcache(['alias'], memoize_timeout=24 * 60 * 60, timeout=24 * 60 * 60, skip_arg=lambda *args: settings.UNIT_TESTING)
def get_indices_by_alias(alias):
    # The cache is not invalidated. So be cautious when using this.
    #   This is designed to be used with ILM indices, where index names
    #   are of a predictable format such as xforms_2016-07-07-000002, xforms_2016-07-07-000001 etc
    #   So, the cache invalidation is not an issue
    from corehq.elastic import get_es_new
    es = get_es_new()
    return list(es.indices.get_alias(alias))


def eager_get_ilm_indices_by_alias(alias):
    # This is same as get_indices_by_alias but also
    #   includes the next to-be-rolled index based on the pattern to handle
    #   the edge case when get_indices_by_alias could be stale
    #   When get_indices_by_alias is stale, this will return true list of indices.
    #   When get_indices_by_alias is not stale, this will
    #   include a non-existent index, so callers should handle it
    # For example this prepends 'xforms_2016-07-07-000004' to a list of
    #   ['xforms_2016-07-07-000003', 'xforms_2016-07-07-000002', 'xforms_2016-07-07-000001']
    #   return by get_indices_by_alias
    indices = get_indices_by_alias(alias)
    index_suffix = indices[0].split("-")[-1]
    next_suffix = str(int(index_suffix) + 1).zfill(len(index_suffix))
    next_index = "-".join(indices[0].split("-")[:-1] + [next_suffix])
    return next_index + [indices]


def _target_ilm_index(doc_id, doc_type, index_info, es_interface):
    # returns the backing ILM index that the doc exists in
    indices = eager_get_ilm_indices_by_alias(index_info.alias)
    doc_exists = False
    found_in = None
    # individual exists query on each index is faster than
    #   ids search on the alias, so iterate through each index
    for index in indices:
        exists = es_interface.doc_exists(index, doc_id, doc_type)
        if exists and doc_exists:
            raise PillowtopIndexingError(
                f"The document {doc_id} is found in more than one indices {found_in}, {index}")
        elif exists:
            doc_exists = True
            found_in = index
    return found_in or index_info.alias


def send_to_elasticsearch(index_info, doc_type, doc_id, es_getter, name, data=None,
                          delete=False, es_merge_update=False, force_lookup=True):
    """
    More fault tolerant es.put method
    kwargs:
        es_merge_update: Set this to True to use Elasticsearch.update instead of Elasticsearch.index
            which merges existing ES doc and current update. If this is set to False, the doc will be replaced
        force_lookup: Set this to True when the doc must be looked up for its existence
            in the ILM backed indices, applicable to ILM indices only.
    """
    alias = index_info.alias
    data = data if data is not None else {}
    current_tries = 0
    es_interface = ElasticsearchInterface(es_getter())
    retries = 1 if settings.UNIT_TESTING else MAX_RETRIES
    propagate_failure = settings.UNIT_TESTING

    while current_tries < retries:
        try:
            if index_info.is_ilm_index and force_lookup:
                target_index = _target_ilm_index(
                    doc_id, doc_type, index_info, es_interface,
                )
            else:
                target_index = alias
            if delete:
                es_interface.delete_doc(target_index, doc_type, doc_id)
            else:
                params = {'retry_on_conflict': 2}
                if es_merge_update:
                    es_interface.update_doc_fields(target_index, doc_type, doc_id, fields=data, params=params,
                        verify_alias=False)
                else:
                    # use the same index API to create or update doc
                    es_interface.index_doc(target_index, doc_type, doc_id, doc=data, params=params,
                        verify_alias=False)
            break
        except ConnectionError as ex:
            current_tries += 1
            pillow_logging.error("[{}] put_robust error {} attempt {}/{}".format(
                name, ex, current_tries, retries))

            if current_tries == retries:
                message = "[{}] Max retry error on {}/{}/{}:\n\n{}".format(
                    name, alias, doc_type, doc_id, traceback.format_exc())
                if propagate_failure:
                    raise PillowtopIndexingError(message)
                else:
                    pillow_logging.error(message)

            time.sleep(math.pow(RETRY_INTERVAL, current_tries))
        except RequestError:
            error_message = (
                "Pillowtop put_robust error [{}]:\n\n{}\n\tpath: {}/{}/{}\n\t{}".format(
                    name, traceback.format_exc(), alias, doc_type, doc_id, list(data))
            )

            if propagate_failure:
                raise PillowtopIndexingError(error_message)
            else:
                pillow_logging.error(error_message)
            break
        except ConflictError:
            break  # ignore the error if a doc already exists when trying to create it in the index
        except NotFoundError:
            break
