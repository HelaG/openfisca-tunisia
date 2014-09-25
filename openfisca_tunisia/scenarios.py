# -*- coding: utf-8 -*-


# OpenFisca -- A versatile microsimulation software
# By: OpenFisca Team <contact@openfisca.fr>
#
# Copyright (C) 2011, 2012, 2013, 2014 OpenFisca Team
# https://github.com/openfisca
#
# This file is part of OpenFisca.
#
# OpenFisca is free software; you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version.
#
# OpenFisca is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program. If not, see <http://www.gnu.org/licenses/>.


import collections
import datetime
import itertools
import json
import logging
import numpy as np
import os
import re
import time
import urllib2
import uuid


from openfisca_core import conv, legislations, simulations
from openfisca_core.scenarios import AbstractScenario

from . import entities


log = logging.getLogger(__name__)
N_ = lambda message: message
year_or_month_or_day_re = re.compile(ur'(18|19|20)\d{2}(-(0[1-9]|1[0-2])(-([0-2]\d|3[0-1]))?)?$')


class Scenario(AbstractScenario):
    axes = None
    test_case = None

    def init_single_entity(self, axes = None, enfants = None, famille = None, foyer_fiscal = None, menage = None,
                           parent1 = None, parent2 = None, date = None):
        if enfants is None:
            enfants = []
        assert parent1 is not None
        foyer_fiscal = foyer_fiscal.copy() if foyer_fiscal is not None else {}
        individus = []
        menage = menage.copy() if menage is not None else {}
        for index, individu in enumerate([parent1, parent2] + (enfants or [])):
            if individu is None:
                continue
            id = individu.get('id')
            if id is None:
                individu = individu.copy()
                individu['id'] = id = 'ind{}'.format(index)
            individus.append(individu)
            if index <= 1:
                foyer_fiscal.setdefault('declarants', []).append(id)
                if index == 0:
                    menage['personne_de_reference'] = id
                else:
                    menage['conjoint'] = id
            else:
                foyer_fiscal.setdefault('personnes_a_charge', []).append(id)
                menage.setdefault('enfants', []).append(id)
        conv.check(self.make_json_or_python_to_attributes())(dict(
            axes = axes,
            date = date,
            test_case = dict(
                foyers_fiscaux = [foyer_fiscal],
                individus = individus,
                menages = [menage],
                ),
            ))
        return self

    def make_json_or_python_to_attributes(self, cache_dir = None, repair = False):
        column_by_name = self.tax_benefit_system.column_by_name

        def json_or_python_to_attributes(value, state = None):
            if value is None:
                return value, None
            if state is None:
                state = conv.default_state

            # First validation and conversion step
            data, error = conv.pipe(
                conv.test_isinstance(dict),
                # TODO: Remove condition below, once every calls uses "date" instead of "year".
                conv.condition(
                    conv.test(lambda value: 'date' not in value),
                    conv.rename_item('year', 'date'),
                    ),
                conv.struct(
                    dict(
                        axes = conv.pipe(
                            conv.test_isinstance(list),
                            conv.uniform_sequence(
                                conv.pipe(
                                    conv.test_isinstance(dict),
                                    conv.struct(
                                        dict(
                                            count = conv.pipe(
                                                conv.test_isinstance(int),
                                                conv.test_greater_or_equal(1),
                                                conv.not_none,
                                                ),
                                            index = conv.pipe(
                                                conv.test_isinstance(int),
                                                conv.test_greater_or_equal(0),
                                                conv.default(0),
                                                ),
                                            max = conv.pipe(
                                                conv.test_isinstance((float, int)),
                                                conv.not_none,
                                                ),
                                            min = conv.pipe(
                                                conv.test_isinstance((float, int)),
                                                conv.not_none,
                                                ),
                                            name = conv.pipe(
                                                conv.test_isinstance(basestring),
                                                conv.test_in(column_by_name),
                                                conv.test(lambda column_name: column_by_name[column_name].dtype in (
                                                    np.float32, np.int16, np.int32),
                                                    error = N_(u'Invalid type for axe: integer or float expected')),
                                                conv.not_none,
                                                ),
                                            ),
                                        ),
                                    ),
                                drop_none_items = True,
                                ),
                            conv.empty_to_none,
                            ),
                        date = conv.pipe(
                            conv.condition(
                                conv.test_isinstance(datetime.date),
                                conv.noop,
                                conv.condition(
                                    conv.test_isinstance(int),
                                    conv.pipe(
                                        conv.test_between(1870, 2099),
                                        conv.function(lambda year: datetime.date(year, 1, 1)),
                                        ),
                                    conv.pipe(
                                        conv.test_isinstance(basestring),
                                        conv.test(year_or_month_or_day_re.match, error = N_(u'Invalid date')),
                                        conv.function(lambda birth: u'-'.join((birth.split(u'-') + [u'01', u'01'])[:3])),
                                        conv.iso8601_input_to_date,
                                        ),
                                    ),
                                ),
                            conv.test_between(datetime.date(1870, 1, 1), datetime.date(2099, 12, 31)),
                            # TODO: Check that date is valid in params.
                            conv.not_none,
                            ),
                        legislation_url = conv.pipe(
                            conv.test_isinstance(basestring),
                            conv.make_input_to_url(error_if_fragment = True, full = True, schemes = ('http', 'https')),
                            ),
                        test_case = conv.pipe(
                            conv.test_isinstance(dict),  # Real test is done below, once date is known.
                            conv.not_none,
                            ),
                        ),
                    ),
                )(value, state = state)
            if error is not None:
                return data, error

            # Second validation and conversion step
            data, error = conv.struct(
                dict(
                    test_case = self.make_json_or_python_to_test_case(date = data['date'], repair = repair),
                    ),
                default = conv.noop,
                )(data, state = state)
            if error is not None:
                return data, error

            # Third validation and conversion step
            errors = {}
            if data['axes'] is not None:
                for axis_index, axis in enumerate(data['axes']):
                    if axis['min'] >= axis['max']:
                        errors.setdefault('axes', {}).setdefault(axis_index, {})['max'] = state._(
                            u"Max value must be greater than min value")
                    column = column_by_name[axis['name']]
                    entity_class = entities.entity_class_by_symbol[column.entity]
                    if axis['index'] >= len(data['test_case'][entity_class.key_plural]):
                        errors.setdefault('axes', {}).setdefault(axis_index, {})['index'] = state._(
                            u"Index must be lower than {}").format(len(data['test_case'][entity_class.key_plural]))
            if errors:
                return data, errors

            if data['legislation_url'] is None:
                compact_legislation = None
            else:
                legislation_json = None
                if cache_dir is not None:
                    legislation_uuid_hex = uuid.uuid5(uuid.NAMESPACE_URL, data['legislation_url'].encode('utf-8')).hex
                    legislation_dir = os.path.join(cache_dir, 'legislations', legislation_uuid_hex[:2])
                    legislation_filename = '{}.json'.format(legislation_uuid_hex[2:])
                    legislation_file_path = os.path.join(legislation_dir, legislation_filename)
                    if os.path.exists(legislation_file_path) \
                            and os.path.getmtime(legislation_file_path) > time.time() - 900:  # 15 minutes
                        with open(legislation_file_path) as legislation_file:
                            try:
                                legislation_json = json.load(legislation_file,
                                    object_pairs_hook = collections.OrderedDict)
                            except ValueError:
                                log.exception('Error while reading legislation JSON file: {}'.format(
                                    legislation_file_path))
                if legislation_json is None:
                    request = urllib2.Request(data['legislation_url'], headers = {
                        'User-Agent': 'OpenFisca-Web-API',
                        })
                    try:
                        response = urllib2.urlopen(request)
                    except urllib2.HTTPError:
                        return data, dict(legislation_url = state._(u'HTTP Error while retrieving legislation JSON'))
                    except urllib2.URLError:
                        return data, dict(legislation_url = state._(u'Error while retrieving legislation JSON'))
                    legislation_json, error = conv.pipe(
                        conv.make_input_to_json(object_pairs_hook = collections.OrderedDict),
                        legislations.validate_any_legislation_json,
                        conv.not_none,
                        )(response.read(), state = state)
                    if error is not None:
                        return data, dict(legislation_url = error)
                    if cache_dir is not None:
                        if not os.path.exists(legislation_dir):
                            os.makedirs(legislation_dir)
                        with open(legislation_file_path, 'w') as legislation_file:
                            legislation_file.write(unicode(json.dumps(legislation_json, encoding = 'utf-8',
                                ensure_ascii = False, indent = 2)).encode('utf-8'))
                if legislation_json.get('datesim') is None:
                    dated_legislation_json = legislations.generate_dated_legislation_json(legislation_json,
                        data['date'])
                else:
                    dated_legislation_json = legislation_json
                    legislation_json = None
                compact_legislation = legislations.compact_dated_node_json(dated_legislation_json)
                if self.tax_benefit_system.preprocess_legislation_parameters is not None:
                    self.tax_benefit_system.preprocess_legislation_parameters(compact_legislation)

            self.axes = data['axes']
            self.compact_legislation = compact_legislation
            self.date = data['date']
            self.legislation_url = data['legislation_url']
            self.test_case = data['test_case']
            return self, None

        return json_or_python_to_attributes

    def make_json_or_python_to_test_case(self, date = None, repair = False):
        assert date is not None

        def json_or_python_to_test_case(value, state = None):
            if value is None:
                return value, None
            if state is None:
                state = conv.default_state

            column_by_name = self.tax_benefit_system.column_by_name

            # First validation and conversion step
            test_case, error = conv.pipe(
                conv.test_isinstance(dict),
                conv.struct(
                    dict(
                        foyers_fiscaux = conv.pipe(
                            conv.condition(
                                conv.test_isinstance(list),
                                conv.pipe(
                                    conv.uniform_sequence(
                                        conv.test_isinstance(dict),
                                        drop_none_items = True,
                                        ),
                                    conv.function(lambda values: collections.OrderedDict(
                                        (value.pop('id', index), value)
                                        for index, value in enumerate(values)
                                        )),
                                    ),
                                ),
                            conv.test_isinstance(dict),
                            conv.uniform_mapping(
                                conv.pipe(
                                    conv.test_isinstance((basestring, int)),
                                    conv.not_none,
                                    ),
                                conv.pipe(
                                    conv.test_isinstance(dict),
                                    conv.struct(
                                        dict(itertools.chain(
                                            dict(
                                                declarants = conv.pipe(
                                                    conv.test_isinstance(list),
                                                    conv.uniform_sequence(
                                                        conv.test_isinstance((basestring, int)),
                                                        drop_none_items = True,
                                                        ),
                                                    conv.default([]),
                                                    ),
                                                personnes_a_charge = conv.pipe(
                                                    conv.test_isinstance(list),
                                                    conv.uniform_sequence(
                                                        conv.test_isinstance((basestring, int)),
                                                        drop_none_items = True,
                                                        ),
                                                    conv.default([]),
                                                    ),
                                                ).iteritems(),
                                            (
                                                (column.name, column.json_to_python)
                                                for column in column_by_name.itervalues()
                                                if column.entity == 'foy'
                                                ),
                                            )),
                                        drop_none_values = True,
                                        ),
                                    ),
                                drop_none_values = True,
                                ),
                            conv.default({}),
                            ),
                        individus = conv.pipe(
                            conv.condition(
                                conv.test_isinstance(list),
                                conv.pipe(
                                    conv.uniform_sequence(
                                        conv.test_isinstance(dict),
                                        drop_none_items = True,
                                        ),
                                    conv.function(lambda values: collections.OrderedDict(
                                        (value.pop('id', index), value)
                                        for index, value in enumerate(values)
                                        )),
                                    ),
                                ),
                            conv.test_isinstance(dict),
                            conv.uniform_mapping(
                                conv.pipe(
                                    conv.test_isinstance((basestring, int)),
                                    conv.not_none,
                                    ),
                                conv.pipe(
                                    conv.test_isinstance(dict),
                                    conv.struct(
                                        dict(
                                            (column.name, column.json_to_python)
                                            for column in column_by_name.itervalues()
                                            if column.entity == 'ind' and column.name not in (
                                                'idfoy', 'idmen', 'quifoy', 'quimen')
                                            ),
                                        drop_none_values = True,
                                        ),
                                    ),
                                drop_none_values = True,
                                ),
                            conv.empty_to_none,
                            conv.not_none,
                            ),
                        menages = conv.pipe(
                            conv.condition(
                                conv.test_isinstance(list),
                                conv.pipe(
                                    conv.uniform_sequence(
                                        conv.test_isinstance(dict),
                                        drop_none_items = True,
                                        ),
                                    conv.function(lambda values: collections.OrderedDict(
                                        (value.pop('id', index), value)
                                        for index, value in enumerate(values)
                                        )),
                                    ),
                                ),
                            conv.test_isinstance(dict),
                            conv.uniform_mapping(
                                conv.pipe(
                                    conv.test_isinstance((basestring, int)),
                                    conv.not_none,
                                    ),
                                conv.pipe(
                                    conv.test_isinstance(dict),
                                    conv.struct(
                                        dict(itertools.chain(
                                            dict(
                                                autres = conv.pipe(
                                                    # personnes ayant un lien autre avec la personne de référence
                                                    conv.test_isinstance(list),
                                                    conv.uniform_sequence(
                                                        conv.test_isinstance((basestring, int)),
                                                        drop_none_items = True,
                                                        ),
                                                    conv.default([]),
                                                    ),
                                                conjoint = conv.test_isinstance((basestring, int)),
                                                    # conjoint de la personne de référence
                                                enfants = conv.pipe(
                                                    # enfants de la personne de référence ou de son conjoint
                                                    conv.test_isinstance(list),
                                                    conv.uniform_sequence(
                                                        conv.test_isinstance((basestring, int)),
                                                        drop_none_items = True,
                                                        ),
                                                    conv.default([]),
                                                    ),
                                                personne_de_reference = conv.test_isinstance((basestring, int)),
                                                ).iteritems(),
                                            (
                                                (column.name, column.json_to_python)
                                                for column in column_by_name.itervalues()
                                                if column.entity == 'men'
                                                ),
                                            )),
                                        drop_none_values = True,
                                        ),
                                    ),
                                drop_none_values = True,
                                ),
                            conv.default({}),
                            ),
                        ),
                    ),
                )(value, state = state)
            if error is not None:
                return test_case, error

            # Second validation step
            foyers_fiscaux_individus_id = list(test_case['individus'].iterkeys())
            menages_individus_id = list(test_case['individus'].iterkeys())
            test_case, error = conv.struct(
                dict(
                    foyers_fiscaux = conv.uniform_mapping(
                        conv.noop,
                        conv.struct(
                            dict(
                                declarants = conv.uniform_sequence(conv.test_in_pop(foyers_fiscaux_individus_id)),
                                personnes_a_charge = conv.uniform_sequence(conv.test_in_pop(
                                    foyers_fiscaux_individus_id)),
                                ),
                            default = conv.noop,
                            ),
                        ),
                    menages = conv.uniform_mapping(
                        conv.noop,
                        conv.struct(
                            dict(
                                autres = conv.uniform_sequence(conv.test_in_pop(menages_individus_id)),
                                conjoint = conv.test_in_pop(menages_individus_id),
                                enfants = conv.uniform_sequence(conv.test_in_pop(menages_individus_id)),
                                personne_de_reference = conv.test_in_pop(menages_individus_id),
                                ),
                            default = conv.noop,
                            ),
                        ),
                    ),
                default = conv.noop,
                )(test_case, state = state)

            if repair:
                # Affecte à un foyer fiscal chaque individu qui n'appartient à aucun d'entre eux.
                new_foyer_fiscal = dict(
                    declarants = [],
                    personnes_a_charge = [],
                    )
                new_foyer_fiscal_id = None
                for individu_id in foyers_fiscaux_individus_id[:]:

                    # Tente d'affecter l'individu à un foyer fiscal d'après son ménage.
                    menage_id, menage, menage_role = find_menage_and_role(test_case, individu_id)
                    if menage_role == u'personne_de_reference':
                        conjoint_id = menage[u'conjoint']
                        if conjoint_id is not None:
                            foyer_fiscal_id, foyer_fiscal, other_role = find_foyer_fiscal_and_role(test_case,
                                conjoint_id)
                            if other_role == u'declarants' and len(foyer_fiscal[u'declarants']) == 1:
                                # Quand l'individu n'est pas encore dans un foyer fiscal, mais qu'il est personne de
                                # référence dans un ménage, qu'il y a un conjoint dans ce ménage et que ce
                                # conjoint est seul déclarant dans un foyer fiscal, alors ajoute l'individu comme
                                # autre déclarant de ce foyer fiscal.
                                foyer_fiscal[u'declarants'].append(individu_id)
                                foyers_fiscaux_individus_id.remove(individu_id)
                    elif menage_role == u'conjoint':
                        personne_de_reference_id = menage[u'personne_de_reference']
                        if personne_de_reference_id is not None:
                            foyer_fiscal_id, foyer_fiscal, other_role = find_foyer_fiscal_and_role(test_case,
                                personne_de_reference_id)
                            if other_role == u'declarants' and len(foyer_fiscal[u'declarants']) == 1:
                                # Quand l'individu n'est pas encore dans un foyer fiscal, mais qu'il est conjoint
                                # dans un ménage, qu'il y a une personne de référence dans ce ménage et que
                                # cette personne est seul déclarant dans un foyer fiscal, alors ajoute l'individu
                                # comme autre déclarant de ce foyer fiscal.
                                foyer_fiscal[u'declarants'].append(individu_id)
                                foyers_fiscaux_individus_id.remove(individu_id)
                    elif menage_role == u'enfants' and (menage['personne_de_reference'] is not None
                            or menage[u'conjoint'] is not None):
                        for other_id in (menage['personne_de_reference'], menage[u'conjoint']):
                            if other_id is None:
                                continue
                            foyer_fiscal_id, foyer_fiscal, other_role = find_foyer_fiscal_and_role(test_case,
                                other_id)
                            if other_role == u'declarants':
                                # Quand l'individu n'est pas encore dans un foyer fiscal, mais qu'il est enfant dans
                                # un ménage, qu'il y a une personne à charge ou un conjoint dans ce ménage et que
                                # celui-ci est déclarant dans un foyer fiscal, alors ajoute l'individu comme
                                # personne à charge de ce foyer fiscal.
                                foyer_fiscal[u'declarants'].append(individu_id)
                                foyers_fiscaux_individus_id.remove(individu_id)
                                break

                    if individu_id in foyers_fiscaux_individus_id:
                        # L'individu n'est toujours pas affecté à un foyer fiscal.
                        individu = test_case['individus'][individu_id]
                        age = find_age(individu, date)
                        if len(new_foyer_fiscal[u'declarants']) < 2 and (age is None or age >= 18):
                            new_foyer_fiscal[u'declarants'].append(individu_id)
                        else:
                            new_foyer_fiscal[u'personnes_a_charge'].append(individu_id)
                        if new_foyer_fiscal_id is None:
                            new_foyer_fiscal_id = unicode(uuid.uuid4())
                            test_case[u'foyers_fiscaux'][new_foyer_fiscal_id] = new_foyer_fiscal
                        foyers_fiscaux_individus_id.remove(individu_id)

                # Affecte à un ménage chaque individu qui n'appartient à aucun d'entre eux.
                new_menage = dict(
                    autres = [],
                    conjoint = None,
                    enfants = [],
                    personne_de_reference = None,
                    )
                new_menage_id = None
                for individu_id in menages_individus_id[:]:
                    # Tente d'affecter l'individu à un ménage d'après son foyer fiscal.
                    foyer_fiscal_id, foyer_fiscal, foyer_fiscal_role = find_foyer_fiscal_and_role(test_case,
                        individu_id)
                    if foyer_fiscal_role == u'declarants' and len(foyer_fiscal[u'declarants']) == 2:
                        for declarant_id in foyer_fiscal[u'declarants']:
                            if declarant_id != individu_id:
                                menage_id, menage, other_role = find_menage_and_role(test_case, declarant_id)
                                if other_role == u'personne_de_reference' and menage[u'conjoint'] is None:
                                    # Quand l'individu n'est pas encore dans un ménage, mais qu'il est déclarant
                                    # dans un foyer fiscal, qu'il y a un autre déclarant dans ce foyer fiscal et que
                                    # cet autre déclarant est personne de référence dans un ménage et qu'il n'y a
                                    # pas de conjoint dans ce ménage, alors ajoute l'individu comme conjoint de ce
                                    # ménage.
                                    menage[u'conjoint'] = individu_id
                                    menages_individus_id.remove(individu_id)
                                elif other_role == u'conjoint' and menage[u'personne_de_reference'] is None:
                                    # Quand l'individu n'est pas encore dans un ménage, mais qu'il est déclarant
                                    # dans une foyer fiscal, qu'il y a un autre déclarant dans ce foyer fiscal et
                                    # que cet autre déclarant est conjoint dans un ménage et qu'il n'y a pas de
                                    # personne de référence dans ce ménage, alors ajoute l'individu comme personne
                                    # de référence de ce ménage.
                                    menage[u'personne_de_reference'] = individu_id
                                    menages_individus_id.remove(individu_id)
                                break
                    elif foyer_fiscal_role == u'personnes_a_charge' and foyer_fiscal[u'declarants']:
                        for declarant_id in foyer_fiscal[u'declarants']:
                            menage_id, menage, other_role = find_menage_and_role(test_case, declarant_id)
                            if other_role in (u'personne_de_reference', u'conjoint'):
                                # Quand l'individu n'est pas encore dans un ménage, mais qu'il est personne à charge
                                # dans un foyer fiscal, qu'il y a un déclarant dans ce foyer fiscal et que ce
                                # déclarant est personne de référence ou conjoint dans un ménage, alors ajoute
                                # l'individu comme enfant de ce ménage.
                                menage[u'enfants'].append(individu_id)
                                menages_individus_id.remove(individu_id)
                                break

                    if individu_id in menages_individus_id:
                        # L'individu n'est toujours pas affecté à un ménage.
                        if new_menage[u'personne_de_reference'] is None:
                            new_menage[u'personne_de_reference'] = individu_id
                        elif new_menage[u'conjoint'] is None:
                            new_menage[u'conjoint'] = individu_id
                        else:
                            new_menage[u'enfants'].append(individu_id)
                        if new_menage_id is None:
                            new_menage_id = unicode(uuid.uuid4())
                            test_case[u'menages'][new_menage_id] = new_menage
                        menages_individus_id.remove(individu_id)

            remaining_individus_id = set(foyers_fiscaux_individus_id).union(menages_individus_id)
            if remaining_individus_id:
                if error is None:
                    error = {}
                for individu_id in remaining_individus_id:
                    error.setdefault('individus', {})[individu_id] = state._(u"Individual is missing from {}").format(
                        state._(u' & ').join(
                            word
                            for word in [
                                u'foyers_fiscaux' if individu_id in foyers_fiscaux_individus_id else None,
                                u'menages' if individu_id in menages_individus_id else None,
                                ]
                            if word is not None
                            ))
            if error is not None:
                return test_case, error

            # Third validation step
            individu_by_id = test_case['individus']
            test_case, error = conv.struct(
                dict(
                    foyers_fiscaux = conv.pipe(
                        conv.uniform_mapping(
                            conv.noop,
                            conv.struct(
                                dict(
                                    declarants = conv.pipe(
                                        conv.empty_to_none,
                                        conv.not_none,
                                        conv.test(lambda declarants: len(declarants) <= 2,
                                            error = N_(u'A "foyer_fiscal" must have at most 2 "declarants"',
                                            )),
                                        conv.uniform_sequence(
                                            conv.test(lambda individu_id:
                                                find_age(individu_by_id[individu_id], date, default = 100) >= 18,
                                                error = u"Un déclarant d'un foyer fiscal doit être agé d'au moins 18"
                                                    u" ans",
                                                ),
                                            ),
                                        ),
                                    personnes_a_charge = conv.uniform_sequence(
                                        conv.test(lambda individu_id: individu_by_id[individu_id].get('inv', False)
                                            or find_age(individu_by_id[individu_id], date, default = 0) < 25,
                                            error = u"Une personne à charge d'un foyer fiscal doit avoir moins de"
                                                u" 25 ans ou être invalide",
                                            ),
                                        ),
                                    ),
                                default = conv.noop,
                                ),
                            ),
                        conv.empty_to_none,
                        conv.not_none,
                        ),
                    individus = conv.uniform_mapping(
                        conv.noop,
                        conv.struct(
                            dict(
                                birth = conv.test(lambda birth: date - birth >= datetime.timedelta(0),
                                    error = u"L'individu doit être né au plus tard le jour de la simulation",
                                    ),
                                ),
                            default = conv.noop,
                            drop_none_values = 'missing',
                            ),
                        ),
                    menages = conv.pipe(
                        conv.uniform_mapping(
                            conv.noop,
                            conv.struct(
                                dict(
                                    personne_de_reference = conv.not_none,
                                    ),
                                default = conv.noop,
                                ),
                            ),
                        conv.empty_to_none,
                        conv.not_none,
                        ),
                    ),
                default = conv.noop,
                )(test_case, state = state)

            return test_case, error

        return json_or_python_to_test_case

    @classmethod
    def make_json_to_instance(cls, cache_dir = None, repair = False, tax_benefit_system = None):
        def json_to_instance(value, state = None):
            if value is None:
                return None, None
            self = cls()
            self.tax_benefit_system = tax_benefit_system
            return self.make_json_or_python_to_attributes(cache_dir = cache_dir, repair = repair)(value = value,
                state = state or conv.default_state)

        return json_to_instance

    def new_simulation(self, debug = False, debug_all = False, trace = False):
        simulation = simulations.Simulation(
            compact_legislation = self.compact_legislation,
            date = self.date,
            debug = debug,
            debug_all = debug_all,
            tax_benefit_system = self.tax_benefit_system,
            trace = trace,
            )

        column_by_name = self.tax_benefit_system.column_by_name
        entity_by_key_plural = simulation.entity_by_key_plural
        steps_count = 1
        if self.axes is not None:
            for axis in self.axes:
                steps_count *= axis['count']
        simulation.steps_count = steps_count
        test_case = self.test_case

        foyers_fiscaux = entity_by_key_plural[u'foyers_fiscaux']
        foyers_fiscaux.step_size = foyers_fiscaux_step_size = len(test_case[u'foyers_fiscaux'])
        foyers_fiscaux.count = steps_count * foyers_fiscaux_step_size
        individus = entity_by_key_plural[u'individus']
        individus.step_size = individus_step_size = len(test_case[u'individus'])
        individus.count = steps_count * individus_step_size
        menages = entity_by_key_plural[u'menages']
        menages.step_size = menages_step_size = len(test_case[u'menages'])
        menages.count = steps_count * menages_step_size

        individu_index_by_id = dict(
            (individu_id, individu_index)
            for individu_index, individu_id in enumerate(test_case[u'individus'].iterkeys())
            )
        #
        individus.get_or_new_holder('idfoy').array = idfoy_array = np.empty(steps_count * individus_step_size,
            dtype = column_by_name['idfoy'].dtype)  # foyer_fiscal_index
        individus.get_or_new_holder('quifoy').array = quifoy_array = np.empty(steps_count * individus_step_size,
            dtype = column_by_name['quifoy'].dtype)  # foyer_fiscal_role
        foyers_fiscaux_roles_count = 0
        for foyer_fiscal_index, foyer_fiscal in enumerate(test_case[u'foyers_fiscaux'].itervalues()):
            declarants_id = foyer_fiscal.pop(u'declarants')
            personnes_a_charge_id = foyer_fiscal.pop(u'personnes_a_charge')
            for step_index in range(steps_count):
                individu_index = individu_index_by_id[declarants_id[0]]
                idfoy_array[step_index * individus_step_size + individu_index] \
                    = step_index * foyers_fiscaux_step_size + foyer_fiscal_index
                quifoy_array[step_index * individus_step_size + individu_index] = 0  # vous
                foyer_fiscal_roles_count = 2
                for personne_a_charge_index, personne_a_charge_id in enumerate(personnes_a_charge_id):
                    individu_index = individu_index_by_id[personne_a_charge_id]
                    idfoy_array[step_index * individus_step_size + individu_index] \
                        = step_index * foyers_fiscaux_step_size + foyer_fiscal_index
                    quifoy_array[step_index * individus_step_size + individu_index] = 2 + personne_a_charge_index  # pac
                    foyer_fiscal_roles_count += 1
                if foyer_fiscal_roles_count > foyers_fiscaux_roles_count:
                    foyers_fiscaux_roles_count = foyer_fiscal_roles_count
        foyers_fiscaux.roles_count = foyers_fiscaux_roles_count
        #
        individus.get_or_new_holder('idmen').array = idmen_array = np.empty(steps_count * individus_step_size,
            dtype = column_by_name['idmen'].dtype)  # menage_index
        individus.get_or_new_holder('quimen').array = quimen_array = np.empty(steps_count * individus_step_size,
            dtype = column_by_name['quimen'].dtype)  # menage_role
        menages_roles_count = 0
        for menage_index, menage in enumerate(test_case[u'menages'].itervalues()):
            personne_de_reference_id = menage.pop(u'personne_de_reference')
            conjoint_id = menage.pop(u'conjoint')
            enfants_id = menage.pop(u'enfants')
            autres_id = menage.pop(u'autres')
            for step_index in range(steps_count):
                individu_index = individu_index_by_id[personne_de_reference_id]
                idmen_array[step_index * individus_step_size + individu_index] = step_index * menages_step_size + menage_index
                quimen_array[step_index * individus_step_size + individu_index] = 0  # pref
                menage_roles_count = 2
                for enfant_index, enfant_id in enumerate(itertools.chain(enfants_id, autres_id)):
                    individu_index = individu_index_by_id[enfant_id]
                    idmen_array[step_index * individus_step_size + individu_index] \
                        = step_index * menages_step_size + menage_index
                    quimen_array[step_index * individus_step_size + individu_index] = 2 + enfant_index  # enf
                    menage_roles_count += 1
                if menage_roles_count > menages_roles_count:
                    menages_roles_count = menage_roles_count
        menages.roles_count = menages_roles_count
        #
        individus.get_or_new_holder('noi').array = np.arange(steps_count * individus_step_size,
            dtype = column_by_name['noi'].dtype)
#        individus.get_or_new_holder(entities.Individus.name_key).array = np.array(
#            [individu[entities.Individus.name_key] for individu in test_case[u'individus'].itervalues()],
#            dtype = object)
        used_columns_name = set(
            key
            for individu in test_case[u'individus'].itervalues()
            for key, value in individu.iteritems()
            if value is not None
            )
        for column_name, column in column_by_name.iteritems():
            if column.entity == 'ind' and column_name in used_columns_name \
                    and column_name not in ('idfoy', 'idmen', 'quifoy', 'quimen'):
                cells_iter = (
                    cell if cell is not None else column.default
                    for cell in (
                        individu.get(column_name)
                        for step_index in range(steps_count)
                        for individu in test_case[u'individus'].itervalues()
                        )
                    )
                individus.get_or_new_holder(column_name).array = np.fromiter(cells_iter, dtype = column.dtype) \
                    if column.dtype is not object else np.array(list(cells_iter), dtype = column.dtype)

        used_columns_name = set(
            key
            for foyer_fiscal in test_case[u'foyers_fiscaux'].itervalues()
            for key, value in foyer_fiscal.iteritems()
            if value is not None
            )
        for column_name, column in column_by_name.iteritems():
            if column.entity == 'fam' and column_name in used_columns_name:
                cells_iter = (
                    cell if cell is not None else column.default
                    for cell in (
                        famille.get(column_name)
                        for step_index in range(steps_count)
                        for famille in test_case[u'familles'].itervalues()
                        )
                    )
                familles.get_or_new_holder(column_name).array = np.fromiter(cells_iter, dtype = column.dtype) \
                    if column.dtype is not object else np.array(list(cells_iter), dtype = column.dtype)

#        foyers_fiscaux.get_or_new_holder('id').array = np.array(test_case[u'foyers_fiscaux'].keys(), dtype = object)
        used_columns_name = set(
            key
            for foyer_fiscal in test_case[u'foyers_fiscaux'].itervalues()
            for key, value in foyer_fiscal.iteritems()
            if value is not None
            )
        for column_name, column in column_by_name.iteritems():
            if column.entity == 'foy' and column_name in used_columns_name:
                cells_iter = (
                    cell if cell is not None else column.default
                    for cell in (
                       foyer_fiscal.get(column_name)
                        for step_index in range(steps_count)
                        for foyer_fiscal in test_case[u'foyers_fiscaux'].itervalues()
                        )
                    )
                foyers_fiscaux.get_or_new_holder(column_name).array = np.fromiter(cells_iter, dtype = column.dtype) \
                    if column.dtype is not object else np.array(list(cells_iter), dtype = column.dtype)

#        menages.get_or_new_holder('id').array = np.array(test_case[u'menages'].keys(), dtype = object)
        used_columns_name = set(
            key
            for menage in test_case[u'menages'].itervalues()
            for key, value in menage.iteritems()
            if value is not None
            )
        for column_name, column in column_by_name.iteritems():
            if column.entity == 'men' and column_name in used_columns_name:
                cells_iter = (
                    cell if cell is not None else column.default
                    for cell in (
                        menage.get(column_name)
                        for step_index in range(steps_count)
                        for menage in test_case[u'menages'].itervalues()
                        )
                    )
                menages.get_or_new_holder(column_name).array = np.fromiter(cells_iter, dtype = column.dtype) \
                    if column.dtype is not object else np.array(list(cells_iter), dtype = column.dtype)

        if self.axes is not None:
            if len(self.axes) == 1:
                axis = self.axes[0]
                entity = simulation.entity_by_column_name[axis['name']]
                holder = simulation.get_or_new_holder(axis['name'])
                if holder.array is None:
                    column = entity.column_by_name[axis['name']]
                    holder.array = np.empty(entity.count, dtype = column.dtype)
                    holder.array.fill(column.default)
                holder.array[axis['index']:: entity.step_size] = np.linspace(axis['min'], axis['max'], axis['count'])
            else:
                axes_linspaces = [
                    np.linspace(axis['min'], axis['max'], axis['count'])
                    for axis in self.axes
                    ]
                axes_meshes = np.meshgrid(*axes_linspaces)
                for axis, mesh in zip(self.axes, axes_meshes):
                    entity = simulation.entity_by_column_name[axis['name']]
                    holder = simulation.get_or_new_holder(axis['name'])
                    if holder.array is None:
                        column = entity.column_by_name[axis['name']]
                        holder.array = np.empty(entity.count, dtype = column.dtype)
                        holder.array.fill(column.default)
                    holder.array[axis['index']:: entity.step_size] = mesh.reshape(steps_count)

        return simulation

    def suggest(self):
        test_case = self.test_case
        suggestions = dict()

        for individu_id, individu in test_case['individus'].iteritems():
            if individu.get('age') is None and individu.get('agem') is None and individu.get('birth') is None:
                # Add missing birth date to person (a parent is 40 years old and a child is 10 years old.
                is_declarant = any(individu_id in foyer_fiscal['declarants'] for foyer_fiscal in test_case['foyers_fiscaux'].itervalues())
                birth_year = self.date.year - 40 if is_declarant else self.date.year - 10
                birth = datetime.date(birth_year, 1, 1)
                individu['birth'] = birth
                suggestions.setdefault('test_case', {}).setdefault('individus', {}).setdefault(individu_id, {})[
                    'birth'] = birth.isoformat()

        return suggestions or None

    def to_json(self):
        self_json = collections.OrderedDict()
        if self.axes is not None:
            self_json['axes'] = self.axes
        if self.date is not None:
            self_json['date'] = self.date.isoformat()
        if self.legislation_url is not None:
            self_json['legislation_url'] = self.legislation_url

        test_case = self.test_case

        if test_case is not None:
            column_by_name = self.tax_benefit_system.column_by_name
            test_case_json = collections.OrderedDict()


            foyers_fiscaux_json = collections.OrderedDict()
            for foyer_fiscal_id, foyer_fiscal in (test_case.get('foyers_fiscaux') or {}).iteritems():
                foyer_fiscal_json = collections.OrderedDict()
                declarants = foyer_fiscal.get('declarants')
                if declarants:
                    foyer_fiscal_json['declarants'] = declarants
                personnes_a_charge = foyer_fiscal.get('personnes_a_charge')
                if personnes_a_charge:
                    foyer_fiscal_json['personnes_a_charge'] = personnes_a_charge
                for column_name, variable_value in foyer_fiscal.iteritems():
                    column = column_by_name.get(column_name)
                    if column is not None and column.entity == 'foy':
                        variable_value_json = column.transform_value_to_json(variable_value)
                        if variable_value_json is not None:
                            foyer_fiscal_json[column_name] = variable_value_json
                foyers_fiscaux_json[foyer_fiscal_id] = foyer_fiscal_json
            if foyers_fiscaux_json:
                test_case_json['foyers_fiscaux'] = foyers_fiscaux_json

            individus_json = collections.OrderedDict()
            for individu_id, individu in (test_case.get('individus') or {}).iteritems():
                individu_json = collections.OrderedDict()
                for column_name, variable_value in individu.iteritems():
                    column = column_by_name.get(column_name)
                    if column is not None and column.entity == 'ind':
                        variable_value_json = column.transform_value_to_json(variable_value)
                        if variable_value_json is not None:
                            individu_json[column_name] = variable_value_json
                individus_json[individu_id] = individu_json
            if individus_json:
                test_case_json['individus'] = individus_json

            menages_json = collections.OrderedDict()
            for menage_id, menage in (test_case.get('menages') or {}).iteritems():
                menage_json = collections.OrderedDict()
                personne_de_reference = menage.get('personne_de_reference')
                if personne_de_reference is not None:
                    menage_json['personne_de_reference'] = personne_de_reference
                conjoint = menage.get('conjoint')
                if conjoint is not None:
                    menage_json['conjoint'] = conjoint
                enfants = menage.get('enfants')
                if enfants:
                    menage_json['enfants'] = enfants
                autres = menage.get('autres')
                if autres:
                    menage_json['autres'] = autres
                for column_name, variable_value in menage.iteritems():
                    column = column_by_name.get(column_name)
                    if column is not None and column.entity == 'men':
                        variable_value_json = column.transform_value_to_json(variable_value)
                        if variable_value_json is not None:
                            menage_json[column_name] = variable_value_json
                menages_json[menage_id] = menage_json
            if menages_json:
                test_case_json['menages'] = menages_json

            self_json['test_case'] = test_case_json

        return self_json


def find_famille_and_role(test_case, individu_id):
    for famille_id, famille in test_case['familles'].iteritems():
        for role in (u'parents', u'enfants'):
            if individu_id in famille[role]:
                return famille_id, famille, role
    return None, None, None


def find_foyer_fiscal_and_role(test_case, individu_id):
    for foyer_fiscal_id, foyer_fiscal in test_case['foyers_fiscaux'].iteritems():
        for role in (u'declarants', u'personnes_a_charge'):
            if individu_id in foyer_fiscal[role]:
                return foyer_fiscal_id, foyer_fiscal, role
    return None, None, None


def find_menage_and_role(test_case, individu_id):
    for menage_id, menage in test_case['menages'].iteritems():
        for role in (u'personne_de_reference', u'conjoint'):
            if menage[role] == individu_id:
                return menage_id, menage, role
        for role in (u'enfants', u'autres'):
            if individu_id in menage[role]:
                return menage_id, menage, role
    return None, None, None


def find_age(individu, date, default = None):
    birth = individu.get('birth')
    if birth is not None:
        age = date.year - birth.year
        if date.month < birth.month or date.month == birth.month and date.day < birth.day:
            age -= 1
        return age
    age = individu.get('age')
    if age is not None:
        return age
    age = individu.get('age')
    if age is not None:
        return age
    agem = individu.get('agem')
    if agem is not None:
        return agem / 12.0
    return default
