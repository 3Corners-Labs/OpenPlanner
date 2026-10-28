# Rechtenmatrix

Dit scherm laat zien wat elke rol in het beheerportaal mag, en waar. U opent het via
**Rechtenmatrix** in het menu. Iedere beheerder kan het bekijken. Wat u zelf mag, staat onder
**Mijn rechten**.

## Rollen

Welke rol iemand heeft, en voor welke organisaties of groepen, regelt uw organisatie in de
identity provider (bij de overheid meestal PingFederate). Het portaal leest dat bij het
inloggen. U kunt rollen dus niet in het portaal toekennen; vraag daarvoor uw
identiteitsbeheerder.

| Rol | Bereik | Wat de rol doet |
|---|---|---|
| Superuser | alles | richt de installatie in: organisaties, koppelingen, feestdagen, updates |
| Organisatiebeheerder | eigen organisaties en hun groepen | beheert groepen, planning, teksten, herinneringen en afspraken |
| Groepsbeheerder | eigen groepen | beheert de planning, teksten en afspraken van het eigen team |
| Compliance en privacy (alleen lezen) | eigen organisaties en hun groepen | controleert: leest de auditlog, de bewaartermijnen en de groepsinstellingen, ziet geen persoonsgegevens en kan niets wijzigen |

De rol **Compliance en privacy** is bedoeld voor bijvoorbeeld de functionaris
gegevensbescherming, de CISO of een auditor. In de auditlog ziet deze rol wel het nummer van
een afspraak, maar niet de afspraak zelf.

## Niveaus

Elk recht geldt op één niveau:

- **Portaal**: iets van het portaal zelf, zoals het overzicht of deze matrix.
- **Organisatie**: iets van één organisatie, zoals nieuwe groepen of een sluiting van de hele
  organisatie.
- **Groep**: iets van één groep, zoals de planning, de teksten of de auditlog van die groep.

## Kenmerken

- **Alleen superuser**: dit recht ligt vast. Het raakt meer dan één organisatie of gaat over
  geheimen, zoals wachtwoorden van koppelingen. Geen andere rol kan het krijgen.
- **Persoonsgegevens**: met dit recht ziet u gegevens van burgers, zoals naam en e-mailadres.
  De rol Compliance en privacy krijgt dit recht nooit.

## Deelnemers en koppelingen

Onder de matrix staat wat burgers kunnen zonder in te loggen: met de link uit hun
bevestigingsmail kunnen ze hun afspraak bekijken, en tot de wijzigingstermijn verzetten of
annuleren. Daaronder staan de rechten van gekoppelde systemen, zoals een bot van het
contactcenter. Die rechten kent de superuser per koppeling toe.
