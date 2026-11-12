# Bezetting van een capaciteitspool

Een getelde capaciteitspool zegt hoeveel mensen er werken voor de diensten die uit de pool putten,
en wanneer. Dat stelt u op deze pagina's in: de **tijdzone** van de pool, de **wekelijkse
tijdblokken** en de **uitzonderingen**. U opent ze via **Organisaties → organisatie → Overzicht →
Vaardigheden en capaciteit**, met **Bezetting** naast de pool. Alleen organisatiebeheerders en de
superuser mogen de bezetting van een pool wijzigen (het recht om capaciteit te beheren), elk voor
hun eigen organisaties, en het portaal kan u eerst vragen opnieuw in te loggen. Een pool van een
andere organisatie, een gearchiveerde pool en een bestaande pool (de bezetting van een dienst
zelf) hebben geen bezettingspagina's.

Een pool telt mensen, geen namen. Tel dezelfde persoon niet in een pool en in de eigen bezetting
van een dienst, of in twee pools, op hetzelfde moment: het portaal rekent dan met twee mensen
waar er één is, en kan dat nog niet voor u controleren.

Nog geen enkele boeking leest de bezetting van een pool. U kunt haar al invoeren, wijzigen en de
tekorten bekijken; flows gaan haar in een latere versie gebruiken.

## Tijdzone

De tijdblokken van een pool gelden in de eigen tijdzone van de pool, ook als de diensten die uit de
pool putten in een andere zone werken. Zomer- en wintertijd volgen uit de zone. Kies eerst de
zone: de wekelijkse tijdblokken en uitzonderingen van een pool kunt u pas daarna invoeren.

## Tekorten vooruit

Als er in de pool meer plekken bezet zijn dan er mensen geteld worden, toont de pagina die
momenten voor de komende dagen, met hoeveel mensen er geteld worden en hoeveel plekken bezet zijn.
Hoe ver vooruit wordt gekeken, is de organisatie-instelling **Tekorten vooruitkijken (dagen)**
(standaard 14). Er wordt niets verplaatst of geannuleerd: bestaande afspraken blijven staan, wat u
ook in de bezetting wijzigt. Pas de bezetting aan, of verplaats afspraken zelf.

## Wekelijkse tijdblokken

Een tijdblok loopt van een begin- tot een eindtijd op een weekdag, met het aantal mensen dat
tegelijk geteld wordt. Blokken van één dag mogen elkaar niet overlappen; blokken die aan elkaar
grenzen mogen wel. Vul 0 mensen in voor een lunchpauze. **Wijzigen** past een blok aan,
**Splitsen** knipt het in twee aansluitende blokken (het tweede deel krijgt een eigen aantal en
label, bijvoorbeeld 0 en "Lunch"), **Verwijderen** haalt het weg. Een wijziging geldt voor
afspraken die u vanaf dat moment maakt.

Elk blok zegt waarvoor het bedoeld is: **afspraken**, **direct gesprek** (werk zonder afspraak),
of beide. Afspraken komen nooit in een blok dat voor directe gesprekken is bedoeld, en andersom.
Een blok kan een label hebben dat alleen beheerders zien, en een periode waarin het geldt.

## Uitzonderingen

Een uitzondering vervangt de wekelijkse tijdblokken van één datum: helemaal geen bezetting, of
andere blokken. De datum mag niet in het verleden liggen. De feestdagen uit de kalenders van de
organisatie sluiten de pool ook, tenzij een uitzondering die datum eigen blokken geeft.

Elke wijziging komt in het [auditlog](audit.md) met de code van de pool, nooit met een naam of een
nummer.
