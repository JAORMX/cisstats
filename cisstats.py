#!/usr/bin/env python

import docx
import re
import argparse

from xml.etree import ElementTree

XCCDF12_NS = "http://checklists.nist.gov/xccdf/1.2"
OVAL_NS = "http://oval.mitre.org/XMLSchema/oval-definitions-5"
IGNITION_SYSTEM = "urn:xccdf:fix:script:ignition"
KUBERNETES_SYSTEM = "urn:xccdf:fix:script:kubernetes"
OCIL_SYSTEM = "http://scap.nist.gov/schema/ocil/2"

XCCDF_RULE_PREFIX = "xccdf_org.ssgproject.content_rule_"
XCCDF_PROFILE_PREFIX = "xccdf_org.ssgproject.content_profile_"

SECTION_RE = r'[0-9]+\.[0-9]+(\.[0-9]+)?'
section_re = re.compile(SECTION_RE)

def removeprefix(fullstr, prefix):
    if fullstr.startswith(prefix):
        return fullstr[len(prefix):]
    else:
        return fullstr

class RuleProperties:
    def __init__(self, profile_id):
        self.profile_id = profile_id
        self.rule_id = None
        self.ignition_fix = False
        self.kubernetes_fix = False
        self.ocil_check = False
        self.oval = False
        self.cis_id = ""

    def __repr__(self):
        return "%s: %s: %s" % (self.profile_id, self.cis_id, self.rule_id)

    def __str__(self):
        return "%s: %s: %s" % (removeprefix(self.profile_id, XCCDF_PROFILE_PREFIX), self.cis_id, removeprefix(self.rule_id, XCCDF_RULE_PREFIX))

    def from_element(self, el):
        self.rule_id = el.get("id")
        oval = el.find("./{%s}check[@system=\"%s\"]" %
                         (XCCDF12_NS, OVAL_NS))
        ignition_fix = el.find("./{%s}fix[@system=\"%s\"]" %
                                 (XCCDF12_NS, IGNITION_SYSTEM))
        kubernetes_fix = el.find("./{%s}fix[@system=\"%s\"]" %
                                   (XCCDF12_NS, KUBERNETES_SYSTEM))
        ocil_check = el.find("./{%s}check[@system=\"%s\"]" %
                               (XCCDF12_NS, OCIL_SYSTEM))
        cis_id = el.find("./{%s}reference" %
                           (XCCDF12_NS))

        self.cis_id = cis_id.text
        self.ignition_fix = False if ignition_fix is None else True
        self.kubernetes_fix = False if kubernetes_fix is None else True
        self.ocil_check = False if ocil_check is None else True
        self.oval = False if oval is None else True
        return self


class BenchmarkStats:
    def __init__(self, cis_bench_stats):
        self.missing_ocil = list()
        self.missing_oval = list()
        self.rules = list()

        self.by_id = dict()
        for s in cis_bench_stats:
            self.by_id[s.section] = list()

        self.n_rule_not_implemented = 0
        self.n_rule_implemented = 0

    def add(self, rule):
        if rule.cis_id not in self.by_id.keys():
            raise ValueError("Rule %s does not belong to the CIS profiles" % rule.cis_id)

        self.rules.append(rule)
        self.by_id[rule.cis_id].append(rule)
        if rule.ocil_check == False:
            self.missing_ocil.append(rule)
        if rule.oval == False:
            self.missing_oval.append(rule)

    def compute_stats(self):
        self.n_rule_not_implemented = 0
        self.n_rule_implemented = 0
        for v in self.by_id.values():
            if len(v) == 0:
                self.n_rule_not_implemented += 1
            else:
                self.n_rule_implemented += 1


class XCCDFBenchmark:
    def __init__(self, filepath):
        self.tree = None
        with open(filepath, 'r') as xccdf_file:
            file_string = xccdf_file.read()
            tree = ElementTree.fromstring(file_string)
            self.tree = tree

        self.indexed_rules = {}
        for rule in self.tree.findall(".//{%s}Rule" % (XCCDF12_NS)):
            rule_id = rule.get("id")
            if rule_id is None:
                raise ValueError("Can't index a rule with no id attribute!")

            if rule_id in self.indexed_rules:
                raise ValueError("Multiple rules exist with same id attribute: %s!" % rule_id)

            self.indexed_rules[rule_id] = rule

    def get_rules(self, profile_name):
        xccdf_profile = self.tree.find(".//{%s}Profile[@id=\"%s\"]" %
                                        (XCCDF12_NS, profile_name))
        if xccdf_profile is None:
            raise ValueError("No such profile: %s" % profile_name)

        rules = []
        selects = xccdf_profile.findall("./{%s}select[@selected=\"true\"]" %
                                        XCCDF12_NS)
        for select in selects:
            rule_id = select.get('idref')
            xccdf_rule = self.indexed_rules.get(rule_id)
            if xccdf_rule is None:
                # it could also be a Group
                continue
            rules.append(xccdf_rule)
        return rules


class CisCtrl:
    def __init__(self, full_title):
        rmatch = section_re.match(full_title)
        if rmatch is None:
            raise ValueError("full title %s does not match re" % full_title)

        self.section = rmatch.group()
        self.title = full_title.lstrip(self.section).strip()


class CisDoc:
    def __init__(self, filename):
        self._doc = docx.Document(filename)

    def read_sections(self):
        section_iter = filter(self._section_filter, self._doc.paragraphs)
        sections = []
        for s in section_iter:
            sections.append(CisCtrl(s.text))
        return sections

    def _section_filter(self, paragraph):
        if paragraph.style.name != "Heading 3":
            return False

        m = section_re.match(paragraph.text)
        if m is None:
            return False

        return True


def process_rules(rule_stats, rules,  profile):
    for rule in rules:
        if rule is None:
            continue

        rprop = RuleProperties(profile).from_element(rule)
        if rprop is None:
            continue

        try:
            rule_stats.add(rprop)
        except ValueError as e:
            print(e)

    return rule_stats


def main():
    parser = argparse.ArgumentParser(prog="cisstats.py")

    parser.add_argument("--ds-path", help="The path to the dataStream")
    parser.add_argument("--cis-path", help="The path to the CIS benchmark")
    # TODO: profile list
    parser.add_argument("--profiles",
                        default="xccdf_org.ssgproject.content_profile_cis,xccdf_org.ssgproject.content_profile_cis-node",
                        help="The XCCDF IDs of the profiles to analyze")

    args = parser.parse_args()

    if args.cis_path:
        doc = CisDoc(args.cis_path)
        cis_control_sections = doc.read_sections()

    stats = BenchmarkStats(cis_control_sections)
    if args.ds_path:
        bench = XCCDFBenchmark(args.ds_path)
        for profile in [p for p in args.profiles.split(',')]:
            rules = bench.get_rules(profile)
            stats = process_rules(stats, rules, profile)

    print("* Rules not covered by neither cis.profile nor cis-node.profile")
    for s in cis_control_sections:
        if len(stats.by_id[s.section]) == 0:
            print("\t%s: %s" % (s.section, s.title))
    print()

    print("* Rules with missing OCIL")
    for no_ocil in stats.missing_ocil:
        print("\t" + str(no_ocil))
    print()

    print("* Rules with missing OVAL")
    for no_oval in stats.missing_oval:
        print("\t" + str(no_oval))
    print()

    stats.compute_stats()
    print("* Statistics")
    included_percent = 100.0 * (stats.n_rule_implemented/len(stats.by_id))
    no_ocil_percent = 100.0 * (len(stats.missing_ocil)/len(stats.by_id))
    no_oval_percent = 100.0 * (len(stats.missing_oval)/len(stats.by_id))
    print("\t%d/%d (%.2f%%) of controls present in either by cis.profile or cis-node.profile" % (stats.n_rule_implemented, len(stats.by_id), included_percent))
    print("\t%d/%d (%.2f%%) of controls missing in both cis.profile or cis-node.profile" % (stats.n_rule_not_implemented, len(stats.by_id), 100.0-included_percent))
    print("\t%d/%d (%.2f%%) of controls missing OCIL" % (len(stats.missing_ocil), len(stats.by_id), no_ocil_percent))
    print("\t%d/%d (%.2f%%) of controls missing OVAL" % (len(stats.missing_oval), len(stats.by_id), no_oval_percent))

if __name__ == "__main__":
    main()
