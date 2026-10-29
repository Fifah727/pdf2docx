# Sécurité

L'interface web est conçue pour **un usage local** :
- elle n'écoute que sur `127.0.0.1` et refuse les requêtes dont l'hôte ou l'origine n'est pas local ;
- les PDF sont validés (signature, mot de passe, nombre de pages, taille) avant toute conversion ;
- les fichiers temporaires sont supprimés après 60 minutes ou à la fermeture ;
- une politique CSP stricte est appliquée aux pages.

**Ne l'exposez pas directement sur Internet** (pas de compte, pas de limitation par utilisateur). Pour un service public, placez-la derrière un proxy inverse avec authentification et limitation de débit.

Signaler une vulnérabilité : ouvrez un [avis de sécurité privé](../../security/advisories/new) sur GitHub plutôt qu'une issue publique.
