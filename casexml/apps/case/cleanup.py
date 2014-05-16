from couchdbkit.exceptions import ResourceNotFound
from datetime import datetime
from casexml.apps.case import const
from casexml.apps.case.models import CommCareCase, CommCareCaseAction
from casexml.apps.case.util import get_case_xform_ids, primary_actions
from casexml.apps.case.xform import get_case_updates
from couchforms import fetch_and_wrap_form


def rebuild_case(case_id):
    """
    Given a case ID, rebuild the entire case state based on all existing forms
    referencing it. Useful when things go wrong or when you need to manually
    rebuild a case after archiving / deleting it
    """

    try:
        case = CommCareCase.get(case_id)
        found = True
    except ResourceNotFound:
        case = CommCareCase()
        case._id = case_id
        found = False

    # clear actions, xform_ids, close state, and all dynamic properties
    dynamic_properties = set([k for action in case.actions for k in action.updated_unknown_properties.keys()])
    for k in dynamic_properties:
        try:
            delattr(case, k)
        except KeyError:
            pass

    # already deleted means it was explicitly set to "deleted",
    # as opposed to getting set to that because it has no actions
    already_deleted = case.doc_type == 'CommCareCase-Deleted' and primary_actions(case)
    if not already_deleted:
        case.doc_type = 'CommCareCase'
    case.xform_ids = []
    case.actions = []
    case.closed = False
    case.closed_on = None
    case.closed_by = ''

    form_ids = get_case_xform_ids(case_id)
    forms = [fetch_and_wrap_form(id) for id in form_ids]
    filtered_forms = [f for f in forms if f.doc_type == "XFormInstance"]
    sorted_forms = sorted(filtered_forms, key=lambda f: f.received_on)
    for form in sorted_forms:
        if not found and case.domain is None:
            case.domain = form.domain
        assert form.domain == case.domain

        case_updates = get_case_updates(form)
        filtered_updates = [u for u in case_updates if u.id == case_id]
        for u in filtered_updates:
            case.update_from_case_update(u, form)

    case.xform_ids = [f._id for f in sorted_forms]
    if not case.xform_ids:
        if not found:
            return None
        # there were no more forms. 'delete' the case
        case.doc_type = 'CommCareCase-Deleted'

    # add a "rebuild" action
    case.actions.append(_rebuild_action())
    case.save()
    return case


def _rebuild_action():
    now = datetime.utcnow()
    return CommCareCaseAction(
        action_type=const.CASE_ACTION_REBUILD,
        date=now,
        server_date=now,
    )
