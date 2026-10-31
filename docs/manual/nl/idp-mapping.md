# Koppeling identiteitsprovider

Dit scherm laat zien welke waarden de identiteitsprovider (de inlogdienst van uw organisatie)
moet meesturen om iemand een rol in OpenPlanner te geven. U opent het via **Koppeling
identiteitsprovider** in het menu. Het scherm is alleen-lezen: rechten toekennen of intrekken
doet het IdP-team, in de identiteitsprovider. Hier ziet u wat het moet sturen en wat er
binnenkwam.

## Eén waarde per rol per organisatie of groep

Elke waarde heeft de vorm `voorvoegsel:rol:organisatie` of
`voorvoegsel:rol:organisatie/groep`, bijvoorbeeld `openplanner:org_admin:gem-a` of
`openplanner:group_admin:gem-a/burgerzaken`. De superuser heeft één waarde zonder doel:
`openplanner:superuser`. Een rol geldt alleen voor het doel in de waarde: wie
organisatiebeheerder is in organisatie A en alleen-lezen in organisatie B, krijgt in B nooit
de rechten van A.

Het scherm toont per organisatie en per groep de exacte waarde per rol, hoeveel medewerkers die
waarde bij hun laatste login hadden en wanneer de laatste van hen inlogde. Een waarde die
niemand meer heeft, valt zo op.

## Bron van de rechten

Bovenaan staat waar de rechten vandaan komen:

- **Drie claims**: de oudere koppeling met aparte claims voor rollen, organisaties en groepen.
- **Schaduwmodus**: de drie claims gelden nog; de rechtenclaim wordt bij elke login
  vergeleken en een verschil komt in de auditlog. Zo kan het IdP-team de nieuwe koppeling
  bewijzen voor de overstap.
- **Rechtenclaim**: alleen de waarden van dit scherm gelden.

De keuze hiervan maakt de beheerder van de installatie op de host; het voorvoegsel en de
claimnaam stelt de superuser in bij de inloginstellingen.

## IdP-waarde van een organisatie of groep

De identiteitsprovider noemt een organisatie of groep bij een vaste **IdP-waarde**, niet bij
de naam of het webadres. Een nieuwe organisatie of groep begint met de code van het webadres
als IdP-waarde. De superuser wijzigt de waarde van een organisatie op de pagina van die
organisatie; een organisatiebeheerder wijzigt de waarde van een groep op het tabblad
**Instellingen** van die groep. Het formulier waarschuwt hoeveel medewerkers toegang verliezen
tot de identiteitsprovider de nieuwe waarde stuurt.

## Niet herkende waarden

Onderaan staan de waarden met het voorvoegsel die bij een login binnenkwamen maar geen recht
gaven, met de reden: een typefout in de rol, een onbekende organisatie of groep, een groep die
niet bij de genoemde organisatie hoort, of een doel dat niet bij het bereik van de rol past.
Waarden die een onbekende organisatie noemen, ziet alleen de superuser. Geef zo'n waarde door
aan het IdP-team; op de pagina van de medewerker (**Medewerkers**) staat hetzelfde.

## Downloaden

**Downloaden als tekst** geeft dezelfde lijst als tekstbestand, één waarde per regel, met een
regel commentaar per organisatie en groep, voor het IdP-team. Elke download staat in de
auditlog.
