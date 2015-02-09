"""
A module for loading contexts using context hints.
"""
from collections import defaultdict

from django.db.models.loading import get_model
from manager_utils import id_dict


def get_context_hints_per_source(context_renderers):
    """
    Given a list of context renderers, return a dictionary of context hints per source.
    """
    # Merge the context render hints for each source as there can be multiple context hints for
    # sources depending on the render target. Merging them together involves combining select
    # and prefetch related hints for each context renderer
    context_hints_per_source = defaultdict(lambda: defaultdict(lambda: {
        'app_name': None,
        'model_name': None,
        'select_related': set(),
        'prefetch_related': set(),
    }))
    for cr in context_renderers:
        for key, hints in cr.context_hints.items():
            context_hints_per_source[cr.source][key]['app_name'] = hints['app_name']
            context_hints_per_source[cr.source][key]['model_name'] = hints['model_name']
            context_hints_per_source[cr.source][key]['select_related'].update(hints.get('select_related', []))
            context_hints_per_source[cr.source][key]['prefetch_related'].update(hints.get('prefetch_related', []))

    return context_hints_per_source


def get_querysets_for_context_hints(context_hints_per_source):
    """
    Given a list of context hint dictionaries, return a dictionary
    of querysets for efficient context loading. The return value
    is structured as follows:

    {
        model: queryset,
        ...
    }
    """
    model_select_relateds = defaultdict(set)
    model_prefetch_relateds = defaultdict(set)
    model_querysets = {}
    for context_hints in context_hints_per_source.values():
        for hints in context_hints.values():
            model = get_model(hints['app_name'], hints['model_name'])
            model_querysets[model] = model.objects
            model_select_relateds[model].update(hints.get('select_related', []))
            model_prefetch_relateds[model].update(hints.get('prefetch_related', []))

    # Attach select and prefetch related parameters to the querysets if needed
    for model, queryset in model_querysets.items():
        if model_select_relateds[model]:
            queryset = queryset.select_related(*model_select_relateds[model])
        if model_prefetch_relateds[model]:
            queryset = queryset.prefetch_related(*model_prefetch_relateds[model])
        model_querysets[model] = queryset

    return model_querysets


def dict_find(d, which_key):
    """
    Finds key values in a nested dictionary. Returns a tuple of the dictionary in which
    the key was found along with the value
    """
    # If the starting point is a list, iterate recursively over all values
    if isinstance(d, (list, tuple)):
        for i in d:
            for result in dict_find(i, which_key):
                yield result

    # Else, iterate over all key values of the dictionary
    elif isinstance(d, dict):
        for k, v in d.items():
            if k == which_key:
                yield d, v
            for result in dict_find(v, which_key):
                yield result


def get_model_ids_to_fetch(events, context_hints_per_source):
    """
    Obtains the ids of all models that need to be fetched. Returns a dictionary of models that
    point to sets of ids that need to be fetched. Return output is as follows:

    {
        model: [id1, id2, ...],
        ...
    }
    """
    model_ids_to_fetch = defaultdict(set)

    for event in events:
        context_hints = context_hints_per_source.get(event.source, {})
        for context_key, hints in context_hints.items():
            for d, value in dict_find(event.context, context_key):
                values = value if isinstance(value, list) else [value]
                model_ids_to_fetch[get_model(hints['app_name'], hints['model_name'])].update(
                    v for v in values if isinstance(v, int)
                )

    return model_ids_to_fetch


def fetch_model_data(model_querysets, model_ids_to_fetch):
    """
    Given a dictionary of models to querysets and model IDs to models, fetch the IDs
    for every model and return the objects in the following structure.

    {
        model: {
            id: obj,
            ...
        },
        ...
    }
    """
    return {
        model: id_dict(model_querysets[model].filter(id__in=ids_to_fetch))
        for model, ids_to_fetch in model_ids_to_fetch.items()
    }


def load_fetched_objects_into_contexts(events, model_data, context_hints_per_source):
    """
    Given the fetched model data and the context hints for each source, go through each
    event and populate the contexts with the loaded information.
    """
    for event in events:
        context_hints = context_hints_per_source.get(event.source, {})
        for context_key, hints in context_hints.items():
            model = get_model(hints['app_name'], hints['model_name'])
            for d, value in dict_find(event.context, context_key):
                if isinstance(value, list):
                    for i, model_id in enumerate(d[context_key]):
                        d[context_key][i] = model_data[model].get(model_id)
                else:
                    d[context_key] = model_data[model].get(value)


def load_contexts(events, mediums):
    """
    Given a list of events and mediums, load the context model data into the contexts of the events.
    """
    context_renderer_model = get_model('entity_event', 'ContextRenderer')
    sources = {event.source for event in events}
    render_groups = {medium.render_group for medium in mediums}
    context_renderers = context_renderer_model.objects.filter(source__in=sources, render_group__in=render_groups)

    context_hints_per_source = get_context_hints_per_source(context_renderers)
    model_querysets = get_querysets_for_context_hints(context_hints_per_source)
    model_ids_to_fetch = get_model_ids_to_fetch(events, context_hints_per_source)
    model_data = fetch_model_data(model_querysets, model_ids_to_fetch)
    load_fetched_objects_into_contexts(events, model_data, context_hints_per_source)

    return events
