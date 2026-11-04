# Script de démo : Claude construit des flux, les répare, et apprend à un agent à s'en servir

**Durée :** 30 minutes, en direct. **Public :** des collègues qui l'installeront ensuite.
**Version vidéo :** [`docs/video/power-platform-mcp-fr.mp4`](../video/power-platform-mcp-fr.mp4) - à lancer si le tenant fait des siennes.
**English version:** [`DEMO-SCRIPT.en.md`](DEMO-SCRIPT.en.md).

La phrase que la démo doit faire passer :

> L'API vous dit CE QUI a cassé. Votre propre serveur MCP dit à Claude POURQUOI - et
> Claude peut alors le réparer, le brancher sur un agent, et le livrer dans une solution.

Tout ce qui suit a été exécuté de bout en bout avant d'être écrit. Les durées viennent de
cette répétition ; ajoutez 20 à 40 secondes de réflexion de Claude par prompt.

---

## Préparation

### La veille (une heure, une seule fois)

**Tenant.** Un tenant de démo ou de développement, jamais la production, jamais un client.
Il faut un environnement **avec Dataverse** (les agents Copilot Studio et les solutions y
vivent).

1. **Un profil az dédié au tenant de démo**, pour que votre `az` de tous les jours reste où il est :
   ```powershell
   $env:AZURE_CONFIG_DIR = "$HOME\.azure-demo"
   az login --tenant <id du tenant de démo>
   az account get-access-token --resource https://service.flow.microsoft.com --query expiresOn -o tsv
   ```
   Une date s'affiche : c'est bon.
2. **Le dépôt**, dans le dossier d'où vous ferez la démo :
   ```powershell
   git clone https://github.com/OwnOptic/power-platform-mcp.git
   cd power-platform-mcp
   python -m venv .venv
   .venv\Scripts\python -m pip install -r requirements.txt
   ```
3. **`.env`** à côté de `server.py` :
   ```ini
   PA_TENANT_ID=<id du tenant de démo>
   PA_ENV_ID=<id de l'environnement avec Dataverse>
   ```
   L'id de l'environnement est dans l'URL de make.powerautomate.com une fois l'environnement choisi.
4. **`.mcp.json`** dans ce dossier - trois serveurs locaux plus Microsoft Learn, tous sur
   le profil de démo (chemins absolus) :
   ```json
   {
     "mcpServers": {
       "power-platform":         { "command": "C:/demo/power-platform-mcp/.venv/Scripts/python.exe", "args": ["C:/demo/power-platform-mcp/server.py"],         "env": { "AZURE_CONFIG_DIR": "C:/Users/vous/.azure-demo" } },
       "power-platform-extras":  { "command": "C:/demo/power-platform-mcp/.venv/Scripts/python.exe", "args": ["C:/demo/power-platform-mcp/extras.py"],         "env": { "AZURE_CONFIG_DIR": "C:/Users/vous/.azure-demo" } },
       "power-platform-copilot": { "command": "C:/demo/power-platform-mcp/.venv/Scripts/python.exe", "args": ["C:/demo/power-platform-mcp/copilot_studio.py"], "env": { "AZURE_CONFIG_DIR": "C:/Users/vous/.azure-demo" } },
       "microsoft-learn": { "type": "http", "url": "https://learn.microsoft.com/api/mcp" }
     }
   }
   ```
5. **Un agent à former.** Dans Copilot Studio, dans cet environnement, créez un agent nommé
   **Store Helper** (vide, orchestration générative activée, sans instructions). Publiez-le
   une fois. C'est la seule chose que les outils ne créent pas : un agent naît dans le portail.
6. **pac pour la partie solutions.** `pac auth create --name demo --environment <url de l'org>`
   (connexion dans le navigateur). Puis `pac auth list` et **notez quel profil est actif** -
   resélectionnez-le après la démo.
7. **Répétez chaque séquence une fois**, puis faites la remise à zéro décrite en bas.

### Trente minutes avant

- [ ] Nouvelle session Claude Code **ouverte dans le dossier du dépôt**. `/mcp` montre quatre serveurs, tous connectés.
- [ ] La commande `az account get-access-token ...` de l'étape 1 affiche toujours une date (l'accès conditionnel peut faire expirer la session ; en cas d'AADSTS70043, `az login` maintenant, pas sur scène).
- [ ] Onglet 1 : make.powerautomate.com sur l'environnement de démo. Onglet 2 : Copilot Studio, Store Helper, volet de test ouvert.
- [ ] `pac auth select --name demo`.
- [ ] Police agrandie dans le terminal et le navigateur. Notifications coupées. Teams fermé.

---

## Déroulé

### 0:00 - 2:00 Accroche : les clics

**Écran :** le portail Power Automate, historique d'une exécution en échec.

Cliquez en direct en comptant à voix haute : le flux, l'historique, l'exécution en échec,
déplier l'action, les entrées, les sorties, puis l'action précédente pour trouver la
valeur qui a causé l'erreur.

> « On a tous fait ça. Pendant la demi-heure qui vient, Claude fait toute la boucle :
> construire le flux, le casser, me dire pourquoi, le réparer, le donner à un agent, et
> le livrer dans une solution. Et à la fin, chacun de vous l'installe. »

### 2:00 - 6:00 Ce que c'est, et comment l'installer

**Écran :** le README, puis Claude Code. Tapez `/mcp`.

Quatre serveurs, dont trois de ce dépôt, tous locaux, tous exécutés **en votre nom** :

- `power-platform` - dix outils : construire, exécuter, diagnostiquer.
- `power-platform-extras` - environnements, démarrage et arrêt, annulation, resoumission, solutions.
- `power-platform-copilot` - les agents Copilot Studio, sur les deux harnais : rubriques classiques, ou nouveau harnais GitHub Copilot avec ses skills.
- `microsoft-learn` - le serveur de documentation de Microsoft, pour que Claude cherche les
  actions des connecteurs au lieu de les deviner.

> « Pas d'inscription d'application, pas de secret. Il emprunte mon `az login`. Et il a
> son propre profil az, épinglé sur ce tenant : mon az de tous les jours est connecté
> ailleurs, et ceci ne peut pas y toucher. »

Montrez l'installation en quatre lignes (étapes 2 à 4 de la préparation) : clone, venv,
`pip install -r requirements.txt`, le bloc `.mcp.json`, redémarrer Claude Code.
**N'installez pas en direct** - c'est déjà installé ; vous montrez juste que c'est court.

### 6:00 - 11:00 Séquence 1 : construire, casser, expliquer (`power-platform`)

**Prompt :**
```
Crée un flux nommé "DEMO - nightly batch" à partir de demo-flow.json, lance-le, et dis-moi ce qui s'est passé.
```

Attendu : `create_flow` (environ 3 s), `run_flow`, `list_runs` : **Failed**. Puis, souvent
sans qu'on le demande, `explain_run`. Sinon :

```
Pourquoi a-t-il échoué ?
```

Attendu : `Compute_batches` a échoué, division par zéro, **`batch_size: 0` venant de `Load_settings`**.

> « L'API m'a dit "Failed". L'outil a suivi les liens que suit le portail et a mis la
> valeur en amont à côté de l'erreur. C'est de là que vient le "pourquoi" : un seul appel
> au lieu des clics du début. »

**Prompt :**
```
Corrige-le avec une taille de lot de 4, relance-le, puis compare l'exécution en échec avec celle qui a fonctionné.
```

Attendu : `get_flow`, `update_flow_definition` (il garde la définition précédente - dites-le),
`run_flow`, **Succeeded**, puis `compare_runs` : divergence à `Compute_batches`, changement
de sortie `batch_size 0 -> 4`.

> « 0 et 4 font le même nombre d'octets. Une comparaison naïve dit que ces deux
> exécutions sont identiques. C'était un vrai bug, et la correction est écrite dans la
> docstring de l'outil, pour toujours. »

**Plan B :** un 400 à la création - lisez-le à voix haute. Un 400 que la docstring
avait prévu fait une meilleure démo qu'un 201 propre.

### 11:00 - 18:00 Séquence 2 : donner un outil à l'agent (`power-platform-copilot`)

**Écran :** Copilot Studio, Store Helper. Montrez qu'il est vide : pas d'instructions, pas d'outils.

**Prompt :**
```
Regarde mon agent Store Helper. Ensuite construis un flux d'agent nommé "DEMO - Order status" à partir de demo-agent-flow.json, ajoute-le à Store Helper comme outil nommé "Get order status", mets à jour ses instructions pour qu'il utilise cet outil pour répondre aux questions sur les commandes dans la langue de la question, et publie-le.
```

Attendu, dans l'ordre : `list_agents`, `get_agent`, `create_agent_flow`, `add_flow_tool`,
`update_agent_instructions`, `publish_agent` (environ 35 s - commentez pendant ce temps) :

1. **Pourquoi `create_agent_flow` et pas `create_flow`.** Copilot Studio n'appelle que des
   flux de solution. Un flux créé par l'API Power Automate n'a aucune ligne dans
   Dataverse : un outil d'agent qui pointe dessus s'enregistre, se publie même, puis
   échoue devant un utilisateur. L'outil refuse ce cas en donnant la raison.
2. **La description de l'outil** est ce qui permet à l'agent de décider quand l'appeler. Lisez-la à voix haute.
3. **Pas d'accolades dans les instructions.** Copilot Studio lit `{` comme du Power Fx ;
   l'outil les refuse avant d'enregistrer.

**Écran :** Copilot Studio. Actualisez. L'outil et les instructions sont là. Dans le volet
de test, demandez :

```
Où en est la commande SO-1043 ?
```

Attendu : « Retardée à l'entrepôt, livraison prévue lundi prochain, DHL » (venant du flux).
Puis en anglais : `And order SO-1042?` - même outil, réponse en anglais.

**Retour dans Claude Code :**
```
Montre-moi les exécutions du flux de statut de commande et explique la dernière.
```

> « Voilà la preuve que l'agent a appelé le flux : une exécution, avec le numéro de
> commande que l'agent a tiré de ma question. »

**Plan B :** l'agent répond sans appeler l'outil - dites-le, et lancez
`Rends la description de l'outil plus explicite sur les numéros de commande et republie.`
C'est la leçon honnête : la description, c'est le prompt.

### 18:00 - 25:00 Séquence 3 : skills, schémas et solutions (`pac`)

**Écran :** `.claude/skills/`. Ouvrez `solution-from-base/SKILL.md`.

> « Les docstrings apprennent à Claude un outil. Les skills lui apprennent une procédure
> entière - et un skill, c'est juste un fichier markdown avec une description qui dit
> quand l'utiliser. »

**Prompt :**
```
Avec le skill dataverse-schema, crée le schéma de schema/orders.example.json dans cet environnement. Montre-moi d'abord le plan.
```

Attendu : le plan (éditeur, solution `PAMCPDemo`, table `pmd_order`, sept colonnes et une
recherche), puis l'application (environ 1 min 30 - commentez : « chaque colonne est un
appel de métadonnées, et le script publie à la fin »).

**Prompt :**
```
Avec le skill solution-from-base, exporte PAMCPDemo avec pac, décompresse-la, ajoute à Order une colonne texte "Tracking number" en copiant une colonne existante, incrémente la version, recompresse-la et réimporte-la. Puis prouve que la colonne existe.
```

Attendu : `pac auth list` (il vérifie où il pointe avant toute chose), export, unpack, une
modification de `Entity.xml`, `Solution.xml` 1.0.0.0 -> 1.1.0.0, pack, import (environ
2 min), puis une vérification dans Dataverse que `pmd_trackingnumber` existe.

> « C'est le scénario "partir de la solution de quelqu'un". Déposez le .zip d'un collègue
> dans le dossier et dites "utilise-la comme base" - même skill, il saute l'export. »

**Plan B :** l'import est lent. Passez à la séquence 4 pendant qu'il tourne et revenez à la preuve ensuite.

### 25:00 - 28:00 Séquence 4 : écrire son propre skill

**Écran :** un nouveau fichier, `.claude/skills/hello-flow/SKILL.md`. Tapez-le en direct :

```markdown
---
name: hello-flow
description: Create a connector-free test flow and run it. Use when asked for a smoke test, a hello world flow, or to check the setup works.
---

1. create_flow from demo-flow.json, with batch_size set to 1 so it succeeds.
2. run_flow, then list_runs until it finishes. Say Succeeded or explain_run it.
3. delete_flow with confirm=True. Report the flow name and how long it took.
```

Nouvelle session, puis : `Fais un test rapide de mon installation.` - il charge le skill tout seul.

> « Trois règles. La description est le déclencheur : écrivez les mots que les gens
> disent vraiment. Le corps est une procédure, pas de la documentation. Et notez ce qui
> a mal tourné, pas ce que dit la doc. »

### 28:00 - 30:00 À retenir, et installation

1. L'authentification et le transport sont banals. Le façonnage et les docstrings sont ce qui vous appartient.
2. Les agents appellent des flux **de solution**. Construisez-les ainsi, décrivez bien l'outil, publiez.
3. Les skills transforment une procédure en quelque chose que toute l'équipe peut demander.

Dépôt : **github.com/OwnOptic/power-platform-mcp** - README, « The short version » : dix
minutes pour installer. Puis les questions.

---

## Remise à zéro entre deux répétitions

Dans Claude Code, avec `power-platform-extras` connecté :
```
Supprime tous les flux dont le nom commence par "DEMO - ", avec confirmation.
```
Dans Copilot Studio : Store Helper, Outils, supprimer « Get order status » ; vider les
instructions ; publier. La solution PAMCPDemo : supprimez-la depuis make.powerapps.com >
Solutions si vous voulez un environnement propre (la table part avec), ou gardez-la -
relancer l'étape du schéma indique alors que tout existe déjà, ce qui fait aussi une bonne
démo. Enfin : `pac auth select` pour revenir à votre profil habituel.

---

## Questions probables

**« C'est sûr ? »** Il agit en votre nom, il peut donc faire tout ce que vous pouvez faire :
tenant de démo, profil az séparé, `PA_TENANT_ID` épinglé. Chaque outil est marqué lecture
seule ou non, et `delete_flow` demande deux fois.

**« Pourquoi pas le MCP Power Automate de Microsoft ? »** Utilisez-le quand il vous suffit -
il est plus large. Ses outils d'exécution disent où ça a cassé, pas pourquoi : ils ne
suivent jamais le lien vers les sorties. Et votre propre serveur porte vos propres leçons
dans ses docstrings.

**« Il peut aussi créer l'agent ? »** Pas d'ici : un agent se crée dans Copilot Studio.
Tout ce qui suit - instructions, outils, publication - il le fait.

**« Il peut créer des connexions ? »** Non, et rien d'honnête ne le peut : une connexion est
une connexion OAuth. Une fois dans le portail, puis `bind_connection` pour toujours.

**« Ça marche avec Claude Desktop ? »** Avec tout client MCP. Claude Desktop prend la même
configuration dans `claude_desktop_config.json`.
