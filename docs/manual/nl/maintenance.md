# Onderhoudsmodus

Met de onderhoudsmodus zet u de hele installatie op alleen lezen terwijl u aan het platform
werkt: een update, een herstel, een koppeling die niet werkt of een incident dat u wilt
onderzoeken zonder dat de gegevens intussen veranderen. U opent het scherm via **Onderhoud**
in het menu. Alleen de superuser ziet dit scherm.

De onderhoudsmodus geldt voor alle organisaties tegelijk. Wilt u één team of één organisatie
tijdelijk geen afspraken laten aannemen, gebruik dan een noodsluiting.

## Wat er gebeurt als de onderhoudsmodus aan staat

- **Beheerders** kunnen alles lezen, maar niets wijzigen. Elke pagina toont bovenaan de balk
  **Onderhoudsmodus**. Een geweigerde wijziging komt in de auditlog met de reden
  **Geweigerd: onderhoudsmodus**.
- **De superuser** kan alleen koppelingen (e-mail, sms, Webex), updates en deze schakelaar
  wijzigen, en **Koppeling controleren** bij een groep gebruiken. Al het andere is ook voor de
  superuser alleen te lezen.

## De onderhoudsmodus aanzetten

<!-- field: reason -->
### Reden

Waarom u de onderhoudsmodus aanzet: een update, het herstel van een eerdere versie, het
herstellen van een koppeling, het onderzoek van een incident of ander onderhoud. De reden
staat op dit scherm en in de auditlog.

<!-- field: expected_end -->
### Verwacht klaar

Wanneer u verwacht klaar te zijn, in de tijdzone van het beheerportaal. Burgers zien dit
tijdstip op de onderhoudspagina. Het is alleen informatie: de onderhoudsmodus gaat niet
vanzelf uit. Laat beide velden leeg als u het niet weet.

Staat de onderhoudsmodus al aan, dan past u met hetzelfde formulier de reden en het verwachte
einde aan. Het tijdstip waarop de onderhoudsmodus aanging, blijft dan hetzelfde.

## De onderhoudsmodus uitzetten

Kies **Onderhoudsmodus uitzetten** zodra u klaar bent. Burgers en beheerders kunnen daarna
direct weer alles wat ze gewend zijn.

Staat de onderhoudsmodus aan **via de server**, dan heeft de beheerder van de server hem
aangezet met de instelling `MAINTENANCE_MODE`, meestal omdat de database zelf onderhoud
krijgt. Die kunt u hier niet uitzetten; vraag de beheerder van de server.
