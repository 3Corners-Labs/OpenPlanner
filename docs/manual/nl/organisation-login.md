# Inloggen: identity providers van een organisatie

Op dit scherm ziet u met welke identity providers (inlogdiensten) de medewerkers van een
organisatie inloggen, en registreert de superuser er een. U opent het via **Organisaties →
organisatie → Overzicht → Inloggen**. Organisatiebeheerders en auditors zien de status van
hun eigen organisatie; registreren, wijzigen, aan- en uitzetten en verwijderen is aan de
superuser en vraagt een recente login.

Zonder eigen provider loggen medewerkers in met een platformaccount. Een eigen provider geeft
alleen rechten binnen de eigen organisatie: een waarde die een andere organisatie of de
superuser noemt, wordt geweigerd (ADR 0083). Een organisatie kan er maximaal twee hebben,
zodat een overstap van de ene naar de andere zonder gat kan; de superuser kan dat maximum
bij **Geavanceerde instellingen** aanpassen.

## Een provider registreren

Registreer OpenPlanner eerst bij de provider, met de **redirect-URI** die op de pagina van de
provider staat en, bij een sleutelpaar, het **JWKS-adres**. Vul daarna het formulier in en
kies **Registreren**. Het discovery-document wordt meteen opgehaald en gecontroleerd: het adres
moet https zijn en openbaar bereikbaar, de issuer moet bij het adres horen en de provider moet
de code-flow met PKCE S256, een asymmetrische handtekening en de gekozen client-authenticatie
ondersteunen. Een afgewezen controle staat met de reden in het auditlog.

Een nieuwe provider staat uit. Doe eerst een **proeflogin**, zet hem dan aan.

<!-- field: key -->
### Sleutel

Kleine letters, cijfers en streepjes, uniek op de installatie en na registratie niet te
wijzigen. Elke identiteit die via deze provider inlogt begint ermee (`idp:<sleutel>:<sub>`),
zodat twee providers nooit dezelfde identiteit opleveren.

<!-- field: discovery_url -->
### Discovery-adres

Het adres van het OpenID Connect discovery-document, eindigend op
`/.well-known/openid-configuration`. Alleen https; een adres naar een intern netwerk of een
doorverwijzing wordt geweigerd.

<!-- field: client_auth -->
### Client-authenticatie

Standaard **sleutelpaar (private_key_jwt)**, zoals het NL GOV-profiel voorschrijft:
OpenPlanner maakt per provider een EC P-256 sleutelpaar, bewaart de privésleutel versleuteld
en publiceert de publieke sleutel op het JWKS-adres. De privésleutel wordt nooit getoond.
Kies **client secret** alleen als de provider geen private_key_jwt kan; het secret wordt
versleuteld opgeslagen en nooit meer getoond.

<!-- field: client_secret -->
### Client secret

Alleen bij client secret. Het veld is altijd leeg; leeg laten behoudt het opgeslagen secret.

<!-- field: scopes -->
### Scopes

Gescheiden door spaties. `openid` is verplicht; `email` is nodig om bij een echte login het
domein van het adres te bevestigen.

<!-- field: entitlement_claim -->
### Entitlement-claim en -prefix

De claim in het ID-token met de rechten, en de prefix die elke waarde moet dragen. De
standaardwaarden zijn die van het platform; de waarden zelf staan op het scherm
**IdP-koppeling**.

## Proeflogin

**Proeflogin** stuurt u naar de provider. Na het inloggen ziet u welke claims terugkwamen (alleen
de namen), of `email_verified` waar was, het domein van het e-mailadres en welke rechten de
entitlement-waarden opleveren. Er wordt geen sessie gestart; uw eigen sessie blijft zoals
hij was. De uitslag staat in het auditlog als `idp.test_login`.

## Tweede factor (MFA-beleid)

Elke provider heeft een **MFA-beleid** (ADR 0100). De provider zelf vraagt om de tweede factor;
OpenPlanner controleert alleen dat het ID-token zegt dat die is gebruikt, met een `amr`- of
`acr`-waarde die u accepteert (leeg: `amr` `mfa otp hwk swk`, geen `acr`). **Uit** negeert het.
**Vastleggen** (de standaard) laat het inloggen doorgaan en schrijft een beveiligingsgebeurtenis,
`admin.login.mfa_missing`, met de sleutel van de provider en zonder persoonsgegevens.
**Afdwingen** weigert het inloggen met de melding dat het account geen tweede factor laat zien.
De proeflogin toont of een tweede factor zichtbaar was; kies **Afdwingen** pas na een proeflogin
met MFA die *ja* zegt, anders sluit u alle beheerders van de organisatie buiten.

## Sleutel roteren, aan- en uitzetten, verwijderen

**Sleutel roteren** maakt een nieuw sleutelpaar; de oude publieke sleutel blijft 24 uur in
het JWKS staan, zodat een lopende uitwisseling niet breekt. **Uitzetten** en **Verwijderen**
beëindigen de logins via deze provider (bij verwijderen ook het secret en de sleutels).
Elke wijziging staat in het auditlog met de sleutel van de provider, nooit met een geheim.
