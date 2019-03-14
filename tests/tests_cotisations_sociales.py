from openfisca_tunisia import CountryTaxBenefitSystem as TunisiaTaxBenefitSystem
from openfisca_core.simulation_builder import SimulationBuilder

from openfisca_tunisia.model.prelevements_obligatoires.cotisations_sociales import TypesRegimeSecuriteSociale

tax_benefit_system = TunisiaTaxBenefitSystem()
nb_individus = 9
simulation_builder = SimulationBuilder()
simulation = simulation_builder.build_default_simulation(tax_benefit_system, count=nb_individus)

period = '2019-01'
simulation.set_input('salaire_de_base', period, [1000, 1000, 1000, 1000, 1000, 1000, 1000, 1000, 1000])

# regimes = [TypesRegimeSecuriteSociale.rtns, TypesRegimeSecuriteSociale.raci, TypesRegimeSecuriteSociale.salarie_cnrps]
regimes = [0, 1, 2, 3, 4, 5, 6, 7, 8]
simulation.set_input('regime_securite_sociale', period, regimes)

cotisations_salarie = simulation.calculate('cotisations_salarie', period)
print(cotisations_salarie)
