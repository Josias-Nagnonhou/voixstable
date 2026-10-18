# VoixStable

Outil web pour obtenir des voix off MiniMax constantes sans gaspiller de crédits :
clonage de plusieurs voix (analyse et nettoyage de l'échantillon), préparation du
texte français (chiffres, abréviations, tirets, références bibliques, dictionnaire),
génération morceau par morceau avec pauses exactes, contrôle qualité automatique,
régénération des seuls morceaux ratés et assemblage en MP3.

Démarrage : `uvicorn app:app --host 0.0.0.0 --port $PORT`
Mode démo : utiliser `demo` comme clé API.
