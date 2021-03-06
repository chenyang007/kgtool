#!/usr/bin/env python
# -*- coding: utf-8 -*-
# Author: Li Ding

# base packages
import os
import sys
import json
import logging
import codecs
import hashlib
import datetime
import time
import argparse
import urlparse
import re
import collections
import glob
import copy

sys.path.insert(0, os.path.abspath('.'))
sys.path.insert(0, os.path.abspath('..'))

from kgtool.core import *  # noqa
from kgtool.stats import stat_kg_report_per_item
from cns_model import preload_schema, CnsSchema, init_report,write_report

# global constants
VERSION = 'v20180724'
CONTEXTS = [os.path.basename(__file__), VERSION]


"""
* run_graphviz: generate a graphviz dot format of a schema
"""

def _get_definition_name(definition):
    return u"{}（{}）".format(definition["name"], definition["nameZh"])

def _add_graphviz_node(definition, graph):
    if definition is None:
        logging.warn("empty definition")
        return

    #logging.info(definition)
    #if definition["name"] == "city":
    #    logging.info(definition)
    #    assert False

    if "rdf:Property" in definition["@type"]:
        p = "property"
    elif "CnsLink" in definition.get("rdfs:subClassOf",[]):
        p = "link"
    else:
        p = "class"
    graph["node_map"][p].add(_get_definition_name(definition))

def _add_graphviz_link(link, graph):
    #logging.info(json4debug(link))
    if link["from"]["name"] == "CnsLink" and link.get("relation",{}).get("name") == "Thing":
        logging.info(json4debug(link))
    graph["link_list"].append(link)
    _add_graphviz_node(link["from"], graph)
    _add_graphviz_node(link["to"], graph)
    if link["type"].endswith("domain_range") :
        _add_graphviz_node(link["relation"], graph)
    elif link["type"] == "template_link":
        _add_graphviz_node(link["relation"], graph)
    elif link["type"] in ["rdfs:subClassOf", "rdfs:subPropertyOf"]:
        pass
    else:
        logging.info(json4debug(link))
        assert False

def _add_domain_range(loaded_schema, definition, graph):
    #domain range relation
    if "rdf:Property" in definition["@type"]:
        if definition.get("rdfs:range") and definition.get("rdfs:domain"):
            range_class = loaded_schema.index_definition_alias.get( definition["rdfs:range"] )
            for domain_ref in definition["rdfs:domain"]:
                domain_class = loaded_schema.index_definition_alias.get( domain_ref )
                if domain_class and range_class:
                    link = {
                        "from": domain_class,
                        "to": range_class,
                        "relation": definition,
                        "type": "property_domain_range"
                    }
                    _add_graphviz_link(link, graph)

def _addSuperClassProperty(loaded_schema, definition, graph):
    #super class/property relation
    for p in ["rdfs:subClassOf", "rdfs:subPropertyOf"]:
        superList = definition.get(p,[])
        for super in superList:
            superDefinition = loaded_schema.index_definition_alias.get(super)
            if superDefinition:
                link = {
                    "from": definition,
                    "to": superDefinition,
                    "type": p,
                }
                _add_graphviz_link(link, graph)

def _add_template_domain_range(loaded_schema, template, graph, map_link_in_out):
    #logging.info(json4debug(template))
    #assert False


    if not template.get("refClass"):
        return
    if not template.get("refProperty"):
        return

    domain_class = loaded_schema.index_definition_alias.get( template["refClass"])
    if not domain_class:
        return

    _property_definition = loaded_schema.index_definition_alias.get(template["refProperty"])
    if not _property_definition:
        return


    range_name = ""
    if template.get("propertyRange"):
        range_name = template["propertyRange"]
        range_class = loaded_schema.index_definition_alias.get( range_name)
    else:
        range_name = _property_definition["rdfs:range"]
        range_class = loaded_schema.index_definition_alias.get( range_name )

    # special processing on  [in, out], system property for property graph
    if range_class is None and range_name.endswith("Enum"):
        logging.warn("missing definition for ENUM {}".format(range_name))
        return

    assert range_class, template

    link_name = domain_class["name"]
    if template["refProperty"] in ["in"]:
        map_link_in_out[link_name]["from"] = range_class
        map_link_in_out[link_name]["relation"] = domain_class
        map_link_in_out[link_name]["type"] = "template_link"
    elif template["refProperty"] in ["out"]:
        map_link_in_out[link_name]["to"] = range_class
    else:
        link = {
            "from": domain_class,
            "to": range_class,
            "relation": _property_definition,
            "type": "template_domain_range"
        }
        _add_graphviz_link(link, graph)

def _filter_compact(graph):
    graph_new = _graph_create()
    for link in graph["link_list"]:
        if link["to"]["category"] == "class-datatype":
            continue
        if link["to"]["category"] == "class-datastructure":
            continue

        if link["to"]["name"] == "CnsLink":
            continue #not need to show super class relation for this case

        #logging.info(json4debug(link))
        graph_new["link_list"].append(link)
        graph_new["node_map"]["class"].add(_get_definition_name(link["from"]))
        graph_new["node_map"]["class"].add(_get_definition_name(link["to"]))

        if link["type"] in ["rdfs:subClassOf", "rdfs:subPropertyOf"]:
            pass
        elif link["type"] in ["property_domain_range"]:
            graph_new["node_map"]["property"].add(_get_definition_name(link["relation"]))
            pass
        elif link["type"] in ["template_link"]:
            graph_new["node_map"]["link"].add(_get_definition_name(link["relation"]))
        else:
            graph_new["node_map"]["property"].add(_get_definition_name(link["relation"]))

    graph_new["node_map"]["class"] = graph_new["node_map"]["class"].difference( graph_new["node_map"]["link"] )
    graph_new["node_map"]["class"] = graph_new["node_map"]["class"].difference( graph_new["node_map"]["property"] )
    return graph_new

def _render_dot_format(graph, name, key, subgraph_name=None):
    # generate graph
    lines = []
    if subgraph_name == None:
        lines.append(u"digraph {} ".format(name))
    else:
        lines.append(u"subgraph cluster_{} ".format(subgraph_name))

    lines.append("{")
    line = "\t# dot -Tpng local/debug/{}_full.dot -olocal/{}_{}.png".format(name, name, key)
    lines.append(line)
    logging.info(line)

    if not subgraph_name is None:
        line = "\tlabel={}".format(subgraph_name)
        lines.append(line)
        #lines.append('\trankdir = "TD"')
    else:
        lines.append('\trankdir = "LR"')
    #nodes
    lines.append('\n\tnode [shape=oval]')
    lines.extend(sorted(list(graph["node_map"]["class"])))
    lines.append("")

    lines.append('\n\tnode [shape=doubleoctagon]')
    lines.extend(sorted(list(graph["node_map"]["link"])))
    lines.append("")

    lines.append('\n\tnode [shape=octagon]')
    lines.extend(sorted(list(graph["node_map"]["property"])))
    lines.append("")

    #links
    for link in graph["link_list"]:
        if link["type"] in ["rdfs:subClassOf", "rdfs:subPropertyOf"]:
            line = u'\t{} -> {}\t [style=dotted]'.format(
                _get_definition_name(link["from"]),
                _get_definition_name(link["to"]) )
            if line not in lines:
                lines.append(line)
        else:
            line = u'\t{} -> {}\t '.format(
                _get_definition_name(link["from"]),
                _get_definition_name(link["relation"]))
            if line not in lines:
                lines.append(line)

            line = u'\t{} -> {}\t '.format(
                _get_definition_name(link["relation"]),
                _get_definition_name(link["to"]))
            if line not in lines:
                lines.append(line)
    lines.append(u"}")

    ret = u'\n'.join(lines)
    return ret

def _graph_create():
    return {
        "link_list":[],
        "node_map":collections.defaultdict(set),
    }

def _graph_update(loaded_schema, schema, graph):
    # preprare data

    for definition in sorted(schema.definition.values(), key=lambda x:x["@id"]):
        # domain range relation
        _add_domain_range(loaded_schema, definition, graph)

        _addSuperClassProperty(loaded_schema, definition, graph)
        pass

    map_link_in_out = collections.defaultdict(dict)
    for template in schema.metadata["template"]:
        _add_template_domain_range(loaded_schema, template, graph, map_link_in_out)

    for key in sorted(map_link_in_out):
        link = map_link_in_out[key]
        _add_graphviz_link(link, graph)
    return graph


def run_graphviz(loaded_schema, name):

    ret = {}

    key = "full"
    graph = _graph_create()
    _graph_update(loaded_schema, loaded_schema, graph)
    ret[key] = _render_dot_format(graph, name, key)

    key = "compact"
    graph_new = _filter_compact(graph)
    ret[key] = _render_dot_format(graph_new, name, key)

    key = "import"
    subgraphs = []
    lines = []
    line = "digraph import_%s {" % (loaded_schema.metadata["name"])
    lines.append(line)
    lines.append('\trankdir = "LR"')

    for schema in loaded_schema.loaded_schema_list:
        graph = _graph_create()
#        if schema.metadata["name"] == "cns_top":
#            continue
        _graph_update(loaded_schema, schema, graph)
        graph_new = _filter_compact(graph)
        subgraph = _render_dot_format(graph_new, None, key, schema.metadata["name"])
        lines.append(subgraph)
    line = "}"
    lines.append(line)
    ret[key] = u'\n'.join(lines)
    #logging.info(ret)
    return ret


def task_graphviz(args):
    #logging.info( "called task_graphviz" )

    filename = args["input_file"]
    loaded_schema = CnsSchema()
    preloaded_schema_list = preload_schema(args)
    loaded_schema.import_jsonld(filename, preloaded_schema_list)

    #validate if we can reproduce the same jsonld based on input
    jsonld_input = file2json(filename)

    name = os.path.basename(args["input_file"]).split(u".")[0]
    name = re.sub(ur"-","_", name)
    ret = run_graphviz(loaded_schema, name)
    for key, lines in ret.items():
        xdebug_file = os.path.join(args["debug_dir"], name+"_"+key+u".dot")
        lines2file([lines], xdebug_file)

if __name__ == "__main__":
    logging.basicConfig(format='[%(levelname)s][%(asctime)s][%(module)s][%(funcName)s][%(lineno)s] %(message)s', level=logging.INFO)
    logging.getLogger("requests").setLevel(logging.WARNING)

    optional_params = {
        '--input_file': 'input file',
        '--dir_schema': 'input schema',
        '--output_file': 'output file',
        '--debug_dir': 'debug directory',
        '--option': 'debug directory',
    }
    main_subtask(__name__, optional_params=optional_params)

"""


    # task 4: graphviz
    python kgtool/cns_graphviz.py task_graphviz --input_file=schema/cns_top.jsonld --debug_dir=local/debug  --dir_schema=schema
    python kgtool/cns_graphviz.py task_graphviz --input_file=schema/cns_schemaorg.jsonld --debug_dir=local/debug  --dir_schema=schema
    python kgtool/cns_graphviz.py task_graphviz --input_file=schema/cns_organization.jsonld --debug_dir=local/debug  --dir_schema=schema

"""
