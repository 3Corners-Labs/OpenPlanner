# Eigen rollen

Met dit scherm maakt de superuser een rol op maat voor een team, bijvoorbeeld een teamleider
die de openingstijden mag wijzigen maar niet de teksten. U opent het via **Rollen aanpassen**
op de [rechtenmatrix](rights-matrix.md). Alleen de superuser ziet dit scherm. Iedere
beheerder ziet in de rechtenmatrix welke eigen rollen er zijn en wie ze heeft gewijzigd.

## Een rol maken

Een eigen rol begint altijd als kopie van een vaste rol. Kies onder **Nieuwe rol** de vaste
rol die het dichtst bij uw wens ligt en kies **Nieuwe rol maken**. U krijgt een formulier met
de rechten van die vaste rol al aangevinkt. Zet daarna rechten aan of uit.

| Kopie van | Bereik | Wat kan erbij |
|---|---|---|
| Organisatiebeheerder | de organisaties van de persoon en hun groepen | alles wat geen slotje heeft |
| Groepsbeheerder | de groepen van de persoon | alles op groepsniveau zonder slotje, zoals groepsinstellingen |
| Compliance en privacy | de organisaties van de persoon en hun groepen | alleen lezen, nooit persoonsgegevens |

Het bereik ligt vast zodra de rol gemaakt is. Een eigen rol kan nooit alles, zoals de
superuser.

<!-- field: name_nl -->
### Naam

Geef de rol een naam in het Nederlands en in het Engels, zoals beheerders hem zien in de
matrix, bij **Mijn rechten** en boven aan elke pagina. Ten hoogste 80 tekens.

<!-- field: claim_value -->
### Waarde in de identity provider

Wie een rol heeft, regelt uw organisatie in de identity provider (bij de overheid meestal
PingFederate). Een eigen rol krijgt daarom een eigen waarde, bijvoorbeeld
`openplanner-teamleider`. Vraag het team dat de identity provider beheert om die waarde toe
te kennen aan de juiste mensen. Zonder spaties of komma's, ten hoogste 128 tekens.

De waarde mag niet gelijk zijn aan die van een vaste rol (zoals de superuser) of aan die van
een andere eigen rol. Zolang de identity provider de waarde niet meestuurt, heeft niemand de
rol. Een nieuwe of gewijzigde waarde werkt na de volgende keer inloggen.

<!-- field: permissions -->
### Rechten

Alleen de rechten die deze rol mag krijgen staan in de lijst. Daaronder staat wat niet te
kiezen is, met de reden:

- **Alleen superuser**: dit recht ligt vast, bijvoorbeeld koppelingen, organisaties en het
  aanpassen van rollen zelf.
- **Buiten het bereik**: een recht van een hele organisatie, zoals nieuwe groepen maken, kan
  niet in een rol die per groep werkt.
- **Wijzigt iets** (alleen bij een kopie van Compliance en privacy): die rol leest alleen.

Sommige rechten hebben een ander recht nodig; dat staat erbij. Wie de planning mag wijzigen,
moet bijvoorbeeld ook de groep mogen bekijken. Het overzicht, **Mijn rechten** en de
rechtenmatrix horen bij elke beheerder. Het portaal zegt bij **Opslaan** wat er ontbreekt.

Een recht met het kenmerk **Persoonsgegevens** toont gegevens van deelnemers. Een kopie van
Compliance en privacy kan dat recht nooit krijgen.

## Wijzigen en verwijderen

Een wijziging van de rechten werkt bij de volgende pagina die iemand opent; opnieuw inloggen
is niet nodig. Elke wijziging krijgt een nieuw versienummer. Heeft iemand anders de rol
intussen gewijzigd, dan zegt het portaal dat en ziet u eerst de nieuwe versie.

Verwijdert u een rol, dan houdt wie hem had er geen rechten van over. Andere rollen van die
persoon blijven werken. Vraag daarna het team van de identity provider de waarde op te
ruimen.

## Vastlegging

Maken, wijzigen en verwijderen worden vastgelegd met tijd, versie, wie het deed en welke
rechten aan of uit gingen. Die geschiedenis staat onder aan de rechtenmatrix, voor iedere
beheerder, en in de auditlog van de superuser.
