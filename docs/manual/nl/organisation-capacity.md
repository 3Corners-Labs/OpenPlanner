# Capaciteit

Een capaciteitspool is de tijd waaruit flows putten, samen met de vaardigheden die iedere
getelde medewerker van de pool heeft. U opent de pools via **Organisaties → organisatie →
Overzicht → Capaciteit**. Organisatiebeheerders en de superuser kunnen ze gebruiken, elk voor
hun eigen organisaties. Voordat u iets wijzigt, kan het portaal u vragen opnieuw in te loggen.

## Soorten pools

- **Bestaand**: de bezetting van een dienst zelf, ongewijzigd. Deze pools zijn gemaakt voor de
  diensten die er al waren. Ze staan hier alleen ter inzage; u kunt ze niet wijzigen of
  archiveren.
- **Eigen**: een pool die u zelf maakte en waaruit één flow put.
- **Gedeeld**: een pool die u zelf maakte en waaruit meer dan één flow put.

Per pool toont de lijst de vaardigheden die iedere getelde medewerker heeft en de flows die
eruit putten. Putten ook flows van andere diensten uit een pool en mag u die diensten niet
zien, dan zegt de lijst "en N flows van andere diensten" en niets meer.

## Tellen

Een pool belooft dat iedere getelde medewerker alle vaardigheden van de pool heeft. Tel dezelfde
persoon niet in twee pools op hetzelfde moment: het portaal kan dat nog niet voor u controleren.
Hoeveel medewerkers per tijdblok in een pool werken, stelt u hier nog niet in; dat volgt zodra
flows de pool gebruiken.

## Een pool toevoegen en wijzigen

<!-- field: pool_name -->
### Naam van de pool

De naam waaraan u de pool herkent, hoogstens 80 tekens.

Onder de naam vinkt u de vaardigheden aan die iedere getelde medewerker heeft. Alleen
vaardigheden die niet zijn gearchiveerd, zijn aan te vinken; een gearchiveerde vaardigheid die de
pool al waarborgt, blijft zichtbaar, als gearchiveerd gemarkeerd, zodat u haar kunt uitvinken.
Een flow die uit de pool put, mag alleen vaardigheden eisen die hier zijn aangevinkt.

## Een pool archiveren

**Archiveer** opent een pagina die zegt wat er gebeurt en om bevestiging vraagt. Een pool waaruit
nog een flow put, in een concept of een gepubliceerde versie, kunt u niet archiveren: de pagina
noemt dan de flows. Haal de pool eerst uit die flows. Gearchiveerde pools staan achter **Toon
gearchiveerde pools**. Elke wijziging komt in de [auditlog](audit.md).
