# Mijn rechten

Dit scherm laat zien wat u mag in het beheerportaal en waar. U opent het via **Mijn rechten** in
het menu. Iedere beheerder die kan inloggen kan het openen; behalve inloggen is er geen recht
voor nodig. Het toont alleen uw eigen rechten. Wat elke rol mag, ziet u in de
[Rechtenmatrix](rights-matrix.md).

De pagina heeft twee onderdelen, die u bovenaan kiest (*Onderdelen van Mijn rechten*): **Mijn
rechten** (dit scherm) en **Mijn sessies**, waar u ziet waar u bent ingelogd en andere sessies
kunt beëindigen (zie [Mijn sessies](my-sessions.md)).

## Uw rollen

De kaart **Rollen** toont uw rollen zoals het portaal ze kent. Ze komen uit de
identiteitsprovider van uw organisatie, die het portaal bij het inloggen uitleest. U kunt ze hier
dus niet wijzigen; vraag het uw team identiteitsbeheer. Een ingebouwde rol kan een naam hebben die
uw organisatie koos. De standaardnaam volgt dan tussen haakjes, zodat u altijd ziet welke rol
bedoeld is. Een eigen rol toont zijn eigen naam (zie [Eigen rollen](roles.md)). Hebt u geen rollen, dan
zegt de pagina dat.

Onder **Rechten per organisatie of groep** ziet u waar elke rol geldt, als korte codes: de rol,
dan de organisatie, en bij een groepsrol de organisatie en de groep, bijvoorbeeld
`org_admin:demo`. De superuser heeft geen organisatie.

## Uw rechten

De tabel onderaan toont wat u mag, in twee kolommen:

- **Recht**: bijvoorbeeld *Bezetting en afwijkende bezetting beheren*.
- **Geldt voor**: waar. *Het beheerportaal* voor rechten die niet over één organisatie of groep
  gaan, *Alle organisaties en groepen*, of de namen van de organisaties en de groepen waar u het
  recht hebt.

De tabel is berekend uit dezelfde matrix als het menu en de knoppen. Een knop die u niet ziet,
hoort dus bij een recht dat niet in deze lijst staat. Sommige rechten hebben een ander recht
nodig: het recht om de bezetting van een groep te beheren werkt bijvoorbeeld alleen samen met het
recht om die groep te bekijken.

## Niet herkende rechten

Heeft uw identiteitsprovider bij uw laatste login waarden meegestuurd die deze installatie niet
herkent, dan verschijnt de kaart **Niet herkende rechten**. Die toont de waarde, de reden en het
moment van ontvangst. Deze waarden geven u geen rechten. Geef ze door aan het team van de
identiteitsprovider; uw pagina onder [Medewerkers](staff.md) toont ze ook. De kaart ontbreekt als
er niets is geweigerd.

## Wat dit scherm niet doet

U kunt hier niets wijzigen. Rollen worden in de identiteitsprovider toegekend, niet in het
portaal. De superuser kan eigen rollen maken binnen de grenzen van een ingebouwde rol; zie
[Eigen rollen](roles.md).
