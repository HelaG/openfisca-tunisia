# -*- coding: utf-8 -*-


from openfisca_tunisia.model.base import *


class code_postal(Variable):
    value_type = int
    entity = Menage
    label = u"Localisation du logement (code postal)"
    definition_period = ETERNITY

class loyer(Variable):
    value_type = int
    entity = Menage
    label = u"Loyer du logement"
    definition_period = YEAR

class statut_occupation_logement(Variable):
    value_type = int
    entity = Menage
    label = u"Statut d'occupation du logement"
    definition_period = YEAR

