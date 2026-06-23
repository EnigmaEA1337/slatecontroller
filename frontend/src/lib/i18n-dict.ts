/**
 * Dictionnaires de traduction — français (source) et anglais.
 *
 * Conventions de rédaction :
 *  - Ton professionnel, neutre, à l'infinitif ou impersonnel.
 *  - Pas de tutoiement, pas de familiarité (« vas-y », « c'est quand »).
 *  - Phrases courtes, dense en information utile, vocabulaire technique
 *    précis.
 *  - Le français est la **source** : toute clé absente en anglais
 *    retombe automatiquement sur la version française.
 *
 * Organisation : un objet imbriqué par feature/page. Les clés sont
 * référencées sous la forme « feature.sous.cle » via `useT()`.
 */

import type { Lang } from "@/hooks/useLang";

/** Tableau de clés / valeurs profondes — typage minimal volontairement
 *  large pour permettre la déclaration imbriquée sans annotation. */
type DictTree = {
  [key: string]: string | DictTree;
};

const FR: DictTree = {
  common: {
    refresh: "Actualiser",
    cancel: "Annuler",
    delete: "Supprimer",
    save: "Enregistrer",
    apply: "Appliquer",
    edit: "Modifier",
    download: "Télécharger",
    yes: "Oui",
    no: "Non",
    loading: "Chargement…",
    error: "Erreur",
    none: "—",
    enabled: "Activé",
    disabled: "Désactivé",
    selectAll: "Tout sélectionner",
    password: "Mot de passe",
    username: "Nom d'utilisateur",
    host: "Hôte",
    port: "Port",
    close: "Fermer",
    confirm: "Confirmer",
  },

  // --- Settings → Apparence -------------------------------------------
  appearance: {
    title: "Apparence",
    subtitle: "Thème visuel · Variables CSS · Persistance locale",
    section_theme: "Choix du thème",
    section_lang: "Langue de l'interface",
    section_palette: "Aperçu de la palette",
    section_note: "Information",
    day_label: "Jour",
    day_desc:
      "Fond clair et accent bleu électrique. Adapté aux environnements très éclairés.",
    night_label: "Nuit",
    night_desc:
      "Fond sombre et accent corail. Thème par défaut, recommandé pour les sessions prolongées.",
    auto_label: "Automatique",
    auto_desc:
      "Suit la préférence du système d'exploitation (macOS, Windows, Linux). Bascule en jour ou en nuit selon le réglage natif de l'OS.",
    note_body:
      "Le réglage est stocké dans le navigateur (localStorage : slate-theme et slate-lang). Chaque opérateur conserve sa propre préférence sur sa propre machine ; aucune préférence n'est synchronisée côté contrôleur.",
    lang_fr: "Français",
    lang_en: "English",
    lang_fr_desc: "Langue d'interface principale.",
    lang_en_desc: "English interface translation.",
  },

  // --- Networks → PCAP ------------------------------------------------
  pcap: {
    title: "Capture réseau (tcpdump)",
    description:
      "Démarre une capture réseau sur le Slate via tcpdump. Phase 1 limitée aux interfaces L2/L3 (br-lan, eth0, tailscale0, apcli*). Le pilote MT7990 ne propose pas le mode monitor ; les captures 802.11 brutes nécessitent un dongle USB externe (Phase 2).",
    form_iface: "Interface",
    form_duration: "Durée",
    form_snaplen: "Snaplen",
    form_filter: "Filtre BPF",
    form_label: "Libellé",
    form_filter_placeholder: "tcp port 443 (optionnel)",
    form_label_placeholder: "Libellé descriptif (optionnel)",
    start: "Démarrer la capture",
    captures_title: "Captures",
    col_id: "ID",
    col_iface: "Interface",
    col_label: "Libellé",
    col_elapsed: "Durée écoulée",
    col_filter: "Filtre",
    col_status: "État",
    col_bytes: "Volume capturé",
    col_actions: "Actions",
    status_planned: "Planifiée",
    status_running: "En cours",
    status_completed: "Terminée",
    status_failed: "Échec",
    status_cancelled: "Annulée",
    action_stop: "Arrêter",
    action_download_title: "Télécharger le fichier pcap",
    action_download_failed: "Échec du téléchargement : {error}",
    action_delete: "Supprimer la capture",
    no_captures:
      "Aucune capture enregistrée. Renseigner les paramètres ci-dessus pour en démarrer une.",
  },

  // --- Protection → DNS -----------------------------------------------
  dns: {
    title: "Protection DNS",
    description:
      "Résolveurs DNS sécurisés (DoT/DoH) et niveaux de protection appliqués par réseau via AdGuard Clients.",
    levels_title: "Niveaux de protection",
    refresh_lists: "Rafraîchir les listes",
    apply_all: "Appliquer tout",
    catalog: {
      famille: {
        name: "Famille",
        description:
          "Filtrage des contenus pour mineurs : pornographie, jeux d'argent, violence, Safe Search forcé. Adapté aux réseaux invités enfants ou familiaux.",
      },
      leger: {
        name: "Léger",
        description:
          "Résolution DNS chiffrée sans filtrage. Recommandé pour les réseaux de confiance ne nécessitant qu'un canal sécurisé.",
      },
      paranoid: {
        name: "Paranoïaque",
        description:
          "Profil maximal : zero-trust DNS, DNSSEC strict, AdGuard avec toutes les listes filtres, parental et Safe Search activés. Recommandé pour les missions sensibles ou le mode lockdown.",
      },
      souverain: {
        name: "Souverain UE",
        description:
          "Résolveurs hébergés exclusivement dans l'Union européenne, sans journalisation, juridiction UE.",
      },
      standard: {
        name: "Standard",
        description:
          "Blocage des contenus malveillants, hameçonnage et pisteurs principaux. Profil par défaut pour la majorité des réseaux.",
      },
    },
    field_default: "Par défaut : {value}",
    field_dot_required: "DoT requis",
    field_doh_required: "DoH requis",
    field_dnssec_required: "DNSSEC requis",
    field_adguard_on: "AdGuard activé",
    field_parental: "Contrôle parental",
    field_safe_search: "Safe Search forcé",
    field_safe_browsing: "Safe Browsing",
    field_blocked: "Services bloqués : {n}",
    field_eu_only: "Juridiction UE uniquement",
    field_extra_blocklists: "+{n} blocklists supplémentaires",

    anti_bypass_title: "Anti-contournement DoT / DoH",
    anti_bypass_intro:
      "Empêche un client de contourner le résolveur local via ses propres canaux DNS chiffrés. Combine deux mécanismes complémentaires :",
    anti_bypass_dot_title:
      "Blocage du port TCP/853 (sens LAN vers WAN)",
    anti_bypass_dot_desc:
      "Empêche les navigateurs et applications qui utilisent un résolveur DoT direct (Cloudflare, Quad9, etc.) de contourner AdGuard. Les clients concernés basculent automatiquement sur le DNS système.",
    anti_bypass_glinet_title:
      "Activation des règles anti-fuite préinstallées GL.iNet",
    anti_bypass_glinet_desc:
      "Le firmware GL.iNet inclut des règles drop_leaked_dns / adgdns pour les zones LAN, guest, wgserver et ovpnserver, mais les laisse désactivées par défaut. L'activation prévient les fuites DNS pendant les rotations de tunnels.",
    anti_bypass_hagezi_title:
      "Liste de blocage HaGeZi DoH/VPN dans AdGuard",
    anti_bypass_hagezi_desc:
      "Filtre les terminaisons DoH publiques (Firefox Secure DNS, Chrome, Brave) et les VPN/proxys courants. Liste mise à jour quotidiennement, environ 600 entrées. Activable depuis la page AdGuard > Filtres.",
    anti_bypass_hagezi_link: "Activable depuis la page AdGuard (feed slug : hagezi-doh-vpn).",
    anti_bypass_footer:
      "Le DoT du Slate vers ses résolveurs amont n'est pas affecté (trafic OUTPUT, non FORWARD).",
    anti_bypass_enable: "Activer l'anti-contournement",
  },

  // --- Networks → Radios ----------------------------------------------
  radio: {
    title: "État live des radios côté Slate",
    col_slot: "Emplacement",
    col_24: "2,4 GHz",
    col_5: "5 GHz",
    col_6: "6 GHz",
    col_mlo: "Multi-Link Operation",
    mlo_caption: "{ifname} associé à {ssid} ({state})",
    state_enabled: "actif",
    state_disabled: "inactif",
    none: "—",
  },

  // --- Tailscale -----------------------------------------------------
  tailscale: {
    title: "Tailscale",
    subtitle:
      "VPN maillé — canal d'administration à distance et accès au LAN domestique depuis le Slate en déplacement.",
    section_connection: "Connexion",
    section_network_test: "Test réseau",
    state_label: "État",
  },

  // --- Proton VPN ----------------------------------------------------
  proton: {
    title: "Proton VPN",
    subtitle: "Tunnel WireGuard via Proton VPN",
    description:
      "Pilotage du tunnel WireGuard fourni par Proton VPN. Sélection des serveurs, application du kill switch et supervision de l'état de connexion.",
  },

  // --- Wifi (catalogue SSIDs) ---------------------------------------
  wifi: {
    title: "SSIDs",
    subtitle: "Catalogue Wi-Fi · {n} SSID",
    subtitle_plural: "Catalogue Wi-Fi · {n} SSIDs",
    description:
      "Liste des SSID gérés par le contrôleur, leur réseau de rattachement, leur sécurité et leur état de diffusion.",
    new: "Nouveau SSID",
  },

  // --- Firewall ------------------------------------------------------
  firewall: {
    title: "Pare-feu",
    subtitle: "Zones, règles personnalisées et journalisation",
    description:
      "Vue des zones UCI, des règles injectées par le contrôleur (préfixe SC_FR_*) et de la traçabilité des actions.",
    section_zones: "Zones",
  },

  // --- Vulnérabilités -----------------------------------------------
  vulnerabilities: {
    title: "Vulnérabilités",
    subtitle:
      "Bill of Materials opkg confronté à OSV et CVE2CAPEC. Identification des CVE applicables et reconstitution des chemins d'attaque.",
  },

  // --- Air Watch -----------------------------------------------------
  air_watch: {
    title: "Surveillance Air Watch",
    subtitle:
      "Audit radio passif : détection des jumeaux malveillants, des trames de désauthentification, des activités WPS et des voisins co-canal à forte puissance.",
  },

  // --- Anti-vol ------------------------------------------------------
  anti_theft: {
    title: "Anti-vol",
    subtitle:
      "Mode autonome de défense : verrouillage du PIN avec escalade, effacement à seuil dépassé, alertes via les webhooks Slate vers le contrôleur.",
  },

  // --- Settings / sous-pages ----------------------------------------
  set_agent: {
    subtitle: "Agent local du Slate",
    title: "Agent",
    description:
      "Déploiement et supervision de l'agent slate-ctrl sur le Slate. Profils en JSON, application hors ligne, intégration du bouton physique.",
  },
  set_communication: {
    subtitle: "Communication",
    title: "Messages",
    description:
      "Activation des messages sur l'écran tactile et envoi de tests à la demande.",
  },
  set_connectivity: {
    subtitle: "Connectivité du contrôleur",
    title: "URLs de rappel",
    description:
      "URL exposées par le contrôleur et consommées par les webhooks Slate (touchscreen watcher, anti-vol).",
  },
  set_controller_https: {
    subtitle: "HTTPS du contrôleur",
    title: "HTTPS",
    description:
      "Exposition du contrôleur en HTTPS sur le tailnet via Tailscale Serve. Certificat Let's Encrypt automatique.",
  },
  set_internal_ca: {
    subtitle: "Autorité de certification interne",
    title: "Autorité de certification",
    description:
      "Autorité racine locale et certificat valide pour 192.168.8.1, utile en environnement hôtelier hors ligne.",
  },
  set_setup_status: {
    subtitle: "État de la configuration",
    title: "Configuration",
    description:
      "Vue agrégée du déploiement : Tailscale, autorité de certification, clé SSH, Slate, rappels.",
  },
  set_ssh_key: {
    subtitle: "Paire de clés SSH",
    title: "Clé SSH",
    description:
      "Authentification par clé exclusive sur le Slate. Génération et déploiement de la clé publique en un clic.",
  },
  set_tailnet_admin: {
    subtitle: "Pairs administrateurs du tailnet",
    title: "Administration tailnet",
    description:
      "Liste blanche des pairs Tailscale autorisés à atteindre les interfaces d'administration. Pilote le drapeau admin_only des profils.",
  },

  // --- Networks / sous-pages ----------------------------------------
  net_interfaces: {
    title: "Interfaces",
    subtitle:
      "État live des interfaces physiques et logiques du Slate (Ethernet, ponts, VLAN, VPN).",
  },
  net_diagnostic: {
    title: "Diagnostic",
    subtitle:
      "Outils de diagnostic réseau : ping, traceroute, lookup DNS, mesure de débit.",
  },
  net_ambient: {
    title: "Veille radio",
    subtitle:
      "Surveillance permanente du spectre Wi-Fi (planificateur APScheduler par bande). Détection lente, faible coût en bande passante.",
  },
  net_surveillance: {
    title: "Sessions de surveillance",
    subtitle:
      "Sessions nommées, chronologie classifiée des points d'accès rencontrés, contexte et notes par session.",
    new_session: "Nouvelle session",
  },
  net_radio_history: {
    title: "Historique radio",
    subtitle:
      "Historique des scans planifiés et manuels. Sert de référence pour comparer une configuration RF dans le temps.",
  },
  net_radio_map: {
    title: "Carte radio",
    subtitle:
      "Visualisation géographique des découvertes RF associées au Slate (Leaflet, données locales).",
  },

  // --- Tailscale audit -----------------------------------------------
  tailscale_audit: {
    title: "Audit Tailscale",
    subtitle:
      "Audit local de la posture du Slate via SSH et lecture de la politique tailnet via l'API d'administration (PAT requis).",
  },

  // --- Remote control (Slate screen) --------------------------------
  remote: {
    title: "Pilotage à distance",
    subtitle: "Écran tactile distant",
    description:
      "Téléchargement de l'écran tactile (capture périodique) et envoi de messages à l'opérateur sur place.",
  },

  // --- Profile form --------------------------------------------------
  profile_form: {
    title_new: "Nouveau profil",
    title_edit: "Édition du profil",
    subtitle:
      "Configuration contextuelle : SSID, VPN, DNS, niveau de filtrage, fond d'écran et règles spécifiques.",
  },

  // --- Wifi orphans --------------------------------------------------
  wifi_orphans: {
    title: "SSIDs orphelins",
    subtitle:
      "Sections wireless présentes sur le Slate mais non référencées par le catalogue du contrôleur. Permet de nettoyer les résidus d'anciens provisionnements ou les tests SSH.",
  },

  // --- Tor -----------------------------------------------------------
  tor: {
    title_audit: "Audit Tor",
    audit_subtitle:
      "État du démon, ponts utilisés, circuits actifs et qualité de la sortie. Vérification de la non-fuite DNS.",
    title_networks: "Tor",
    networks_subtitle:
      "Routage Tor par réseau : transparent pour tout le trafic ou SOCKS uniquement. DNS sur Tor et kill switch optionnels.",
  },

  // --- Login ---------------------------------------------------------
  login: {
    page_label: "Authentification du contrôleur",
    page_title: "Connexion",
    username: "Nom d'utilisateur",
    password: "Mot de passe",
    submit: "Se connecter",
    connecting: "Connexion en cours…",
    err_unauthorized: "Identifiants invalides",
    err_network: "Contrôleur injoignable",
  },

  // --- AdGuard -------------------------------------------------------
  adguard: {
    title: "AdGuard",
    subtitle: "Protection · AdGuard Home",
    description:
      "Filtrage DNS, listes de blocage et statistiques d'exécution.",
  },

  // --- Sécurité / Hardening ------------------------------------------
  security: {
    hub_title: "Audit de sécurité",
    hub_subtitle:
      "Indicateurs agrégés : durcissement, vulnérabilités, qualité du maillage VPN.",
    reliability_label: "Fiabilité du Slate",
    hardening_title: "Durcissement de l'équipement",
    hardening_subtitle:
      "Niveau de durcissement du Slate. Contrôles OpenWrt et GL.iNet évalués selon un référentiel pondéré.",
    vulnerabilities_title: "Vulnérabilités",
    air_watch_title: "Surveillance Air Watch",
    anti_theft_title: "Anti-vol",
  },

  // --- Networks ------------------------------------------------------
  networks: {
    title: "Réseaux",
    subtitle: "Bridges, VLAN, sous-réseaux référencés par les SSID",
    counter: "Catalogue · {n} bridge(s)",
    new: "Nouveau réseau",
    empty: "Aucun réseau défini.",
  },

  // --- Profiles ------------------------------------------------------
  profiles: {
    title: "Profils",
    subtitle: "Profils contextuels",
    counter:
      "{n} profil(s) · « template » correspond aux profils fournis, « user » à ceux créés par l'opérateur.",
    new: "Nouveau profil",
    regenerate_all: "Régénérer les fonds d'écran",
    regenerate_all_title:
      "Régénère les fonds d'écran « accueil » et « verrouillage » de tous les profils selon le thème actif. Les fichiers existants sont remplacés.",
    regenerating: "Génération en cours…",
    regen_success: "{n} fond(s) d'écran régénéré(s)",
    regen_pushed_active: "Profil actif {name} mis à jour sur le Slate (accueil + verrouillage, gl_screen redémarré)",
    regen_pushed_active_failed: "Échec de la propagation vers le Slate : {errors}",
    regen_no_active:
      "Aucun profil actif — activer un profil pour pousser les fonds d'écran sur le Slate.",
    regen_partial_failures: "{n} échec(s) :",
    no_profiles: "Aucun profil n'est défini. Cliquer sur « Nouveau profil ».",
  },

  // --- Devices -------------------------------------------------------
  devices: {
    title: "Équipements",
    subtitle:
      "Équipements GL.iNet pilotés par le contrôleur · adoption et durcissement",
    counter: "Équipements gérés · {n}",
    new: "Nouvel équipement",
    empty:
      "Aucun équipement n'a encore été déclaré. Cliquer sur « Nouvel équipement » pour démarrer l'adoption.",
    form_slug: "Identifiant",
    form_label: "Libellé",
    form_admin_url: "URL d'administration",
    form_slug_placeholder: "slate-mobile",
    form_label_placeholder: "Slate du sac à dos",
    form_admin_url_placeholder: "192.168.8.1",
    action_edit_urls: "Modifier la liste des URL d'administration (LAN, Tailscale, personnalisée)",
    action_reset_status: "Réinitialise l'état local à « en attente ». La configuration du Slate n'est pas modifiée.",
    action_factory_reset: "Action destructive : déclenche un firstboot et un redémarrage du Slate.",
    action_create: "Créer",
    action_cancel: "Annuler",
    action_adopt: "Adopter",
  },

  // --- Dashboard -----------------------------------------------------
  dashboard: {
    title: "Tableau de bord",
    subtitle: "Supervision temps réel",
    refresh: "Actualiser",
    syncing: "Synchronisation…",
    connecting: "Connexion au contrôleur en cours…",
    error_unreachable: "Slate injoignable. {error}",
    status_online: "En ligne",
    status_offline: "Hors ligne",
    label_lan: "LAN",
    label_mac: "MAC",
    label_wan: "WAN",
    label_country: "Pays",
    wan_online: "Connecté",
    wan_offline: "Déconnecté",
    stat_uptime: "Disponibilité",
    stat_clients: "Clients",
    stat_cpu_temp: "Température CPU",
    stat_cpu_cores: "{n} cœurs",
    stat_load_1m: "Charge 1 min",
    stat_load_hint: "5 min {l5} · 15 min {l15}",
    stat_ram: "Mémoire",
    stat_ram_hint: "{value} disponibles",
    services_title: "Services actifs",
    snapshot: "Capture à {time}",
    uptime_days: "{d} j {h} h {m} min",
    uptime_hours: "{h} h {m} min",
    uptime_minutes: "{m} min",
  },

  // --- Navigation latérale (sidebar) ---------------------------------
  nav: {
    brand: "Contrôleur",
    loading: "Chargement…",
    logout: "Se déconnecter",
    user: "Utilisateur",
    section_network: "Réseau",
    section_air_wave: "Air Wave",
    section_audit: "Audit",
    section_vpn: "VPN",
    section_protection: "Protection",
    section_settings: "Paramètres",
    expand: "Déplier",
    collapse: "Replier",
    reliability_tooltip: "Fiabilité du Slate : {percent}% — {label}",
    reliability_unknown: "Fiabilité du Slate : indéterminée",
    item_dashboard: "Tableau de bord",
    item_devices: "Équipements",
    item_profiles: "Profils",
    item_remote_control: "Pilotage à distance",
    item_interfaces: "Interfaces",
    item_diagnostic: "Diagnostic",
    item_networks: "Réseaux",
    item_ssids: "SSIDs",
    item_ssids_orphans: "SSIDs orphelins",
    item_tor: "Tor",
    item_rf_scanner: "Scanner RF",
    item_geo_map: "Carte géographique",
    item_ambient: "Veille radio",
    item_surveillance: "Surveillance",
    item_pcap: "Capture PCAP",
    item_hardening: "Durcissement",
    item_vulnerabilities: "Vulnérabilités",
    item_tailscale_audit: "Audit Tailscale",
    item_tor_audit: "Audit Tor",
    item_air_watch: "Surveillance Air Watch",
    item_anti_theft: "Anti-vol",
    item_proton_vpn: "Proton VPN",
    item_fortinet: "Fortinet — configuration",
    item_fortinet_connect: "Fortinet — connexion",
    item_tailscale: "Tailscale",
    item_adguard: "AdGuard",
    item_dns: "DNS",
    item_firewall: "Pare-feu",
    item_setup_status: "État de la configuration",
    item_ssh_keypair: "Paire de clés SSH",
    item_https_controller: "HTTPS du contrôleur",
    item_internal_ca: "Autorité de certification interne",
    item_tailnet_admin: "Pairs admin tailnet",
    item_callback_urls: "URL de rappel",
    item_communication: "Communication",
    item_local_agent: "Agent local",
    item_appearance: "Apparence",
  },

  // --- Settings → Hub ------------------------------------------------
  settings: {
    title: "Paramètres",
    subtitle: "Configuration globale du contrôleur",
    subsection_count: "{n} sous-section",
    subsection_count_plural: "{n} sous-sections",
    nav_appearance: "Apparence",
    nav_communication: "Communication",
    nav_connectivity: "Connectivité",
    nav_agent: "Agent local",
    nav_ssh: "Clé SSH",
    nav_tailnet: "Administration tailnet",
    nav_https: "HTTPS du contrôleur",
    nav_ca: "Autorité de certification interne",
    nav_setup: "État de la configuration",
    hub: {
      setup_title: "État de la configuration",
      setup_desc:
        "Vue agrégée de l'état du déploiement (Tailscale, autorité de certification, clé SSH, Slate, rappels). Sert de liste de contrôle après chaque installation.",
      ssh_title: "Paire de clés SSH",
      ssh_desc:
        "Authentification par clé exclusive sur le Slate. Génération et déploiement de la clé publique en un clic.",
      https_title: "HTTPS du contrôleur",
      https_desc:
        "Exposition du contrôleur en HTTPS sur le tailnet via Tailscale Serve. Certificat Let's Encrypt automatique, jamais accessible publiquement.",
      ca_title: "Autorité de certification interne",
      ca_desc:
        "Autorité racine locale et certificat valide pour le Slate sur 192.168.8.1, utile en environnement hôtelier hors ligne. L'installation unique du Root CA élimine les avertissements de certificat dans les navigateurs.",
      tailnet_title: "Pairs administrateurs du tailnet",
      tailnet_desc:
        "Liste blanche des pairs Tailscale autorisés à atteindre les interfaces d'administration (LuCI, SSH, AdGuard, contrôleur). Pilote l'indicateur admin_only des profils.",
      communication_title: "Communication",
      communication_desc:
        "Activation des messages sur écran tactile et envoi de tests à la demande.",
      agent_title: "Agent local",
      agent_desc:
        "Déploiement de l'agent slate-ctrl sur le Slate : profils au format JSON, application hors ligne, intégration du bouton physique.",
    },
  },
};

const EN: DictTree = {
  common: {
    refresh: "Refresh",
    cancel: "Cancel",
    delete: "Delete",
    save: "Save",
    apply: "Apply",
    edit: "Edit",
    download: "Download",
    yes: "Yes",
    no: "No",
    loading: "Loading…",
    error: "Error",
    none: "—",
    enabled: "Enabled",
    disabled: "Disabled",
    selectAll: "Select all",
    password: "Password",
    username: "Username",
    host: "Host",
    port: "Port",
    close: "Close",
    confirm: "Confirm",
  },

  appearance: {
    title: "Appearance",
    subtitle: "Visual theme · CSS variables · Local persistence",
    section_theme: "Theme selection",
    section_lang: "Interface language",
    section_palette: "Palette preview",
    section_note: "Information",
    day_label: "Day",
    day_desc:
      "Light background with an electric-blue accent. Recommended for brightly lit environments.",
    night_label: "Night",
    night_desc:
      "Dark background with a coral accent. Default theme, optimal for long operating sessions.",
    auto_label: "Automatic",
    auto_desc:
      "Follows the operating system preference (macOS, Windows, Linux). Switches between day and night based on the OS-level setting.",
    note_body:
      "The setting is stored in the browser (localStorage: slate-theme and slate-lang). Each operator keeps their own preference on their own machine; no preference is synchronised with the controller.",
    lang_fr: "Français",
    lang_en: "English",
    lang_fr_desc: "Primary interface language.",
    lang_en_desc: "English interface translation.",
  },

  pcap: {
    title: "Network capture (tcpdump)",
    description:
      "Starts a network capture on the Slate via tcpdump. Phase 1 is limited to L2/L3 interfaces (br-lan, eth0, tailscale0, apcli*). The MT7990 driver does not expose monitor mode; raw 802.11 captures require an external USB dongle (Phase 2).",
    form_iface: "Interface",
    form_duration: "Duration",
    form_snaplen: "Snaplen",
    form_filter: "BPF filter",
    form_label: "Label",
    form_filter_placeholder: "tcp port 443 (optional)",
    form_label_placeholder: "Descriptive label (optional)",
    start: "Start capture",
    captures_title: "Captures",
    col_id: "ID",
    col_iface: "Interface",
    col_label: "Label",
    col_elapsed: "Elapsed",
    col_filter: "Filter",
    col_status: "Status",
    col_bytes: "Captured",
    col_actions: "Actions",
    status_planned: "Planned",
    status_running: "Running",
    status_completed: "Completed",
    status_failed: "Failed",
    status_cancelled: "Cancelled",
    action_stop: "Stop",
    action_download_title: "Download the pcap file",
    action_download_failed: "Download failed: {error}",
    action_delete: "Delete capture",
    no_captures:
      "No captures recorded. Fill in the form above to start one.",
  },

  dns: {
    title: "DNS protection",
    description:
      "Secure DNS resolvers (DoT/DoH) and protection levels applied per network through AdGuard Clients.",
    levels_title: "Protection levels",
    refresh_lists: "Refresh lists",
    apply_all: "Apply all",
    catalog: {
      famille: {
        name: "Family",
        description:
          "Content filtering for minors: adult content, gambling, violence, forced Safe Search. Suited to guest or family networks.",
      },
      leger: {
        name: "Light",
        description:
          "Encrypted DNS resolution without filtering. Recommended for trusted networks that only need a secure channel.",
      },
      paranoid: {
        name: "Paranoid",
        description:
          "Maximum profile: zero-trust DNS, strict DNSSEC, AdGuard with all filter lists, parental control and Safe Search enabled. Recommended for sensitive missions or lockdown mode.",
      },
      souverain: {
        name: "EU Sovereign",
        description:
          "Resolvers hosted exclusively within the European Union, no logging, EU jurisdiction.",
      },
      standard: {
        name: "Standard",
        description:
          "Blocks malicious content, phishing and major trackers. Default profile for most networks.",
      },
    },
    field_default: "Default: {value}",
    field_dot_required: "DoT required",
    field_doh_required: "DoH required",
    field_dnssec_required: "DNSSEC required",
    field_adguard_on: "AdGuard enabled",
    field_parental: "Parental controls",
    field_safe_search: "Forced Safe Search",
    field_safe_browsing: "Safe Browsing",
    field_blocked: "Blocked services: {n}",
    field_eu_only: "EU jurisdiction only",
    field_extra_blocklists: "+{n} additional blocklists",

    anti_bypass_title: "DoT / DoH bypass prevention",
    anti_bypass_intro:
      "Prevents a client from bypassing the local resolver through its own encrypted DNS channels. Combines two complementary mechanisms:",
    anti_bypass_dot_title:
      "Block TCP/853 (LAN to WAN direction)",
    anti_bypass_dot_desc:
      "Prevents browsers and applications using a direct DoT resolver (Cloudflare, Quad9, etc.) from bypassing AdGuard. Affected clients automatically fall back to the system DNS.",
    anti_bypass_glinet_title:
      "Enable preinstalled GL.iNet anti-leak rules",
    anti_bypass_glinet_desc:
      "GL.iNet firmware ships drop_leaked_dns / adgdns rules for the LAN, guest, wgserver and ovpnserver zones but leaves them disabled by default. Enabling them prevents DNS leaks during tunnel rotations.",
    anti_bypass_hagezi_title:
      "HaGeZi DoH/VPN blocklist in AdGuard",
    anti_bypass_hagezi_desc:
      "Filters public DoH endpoints (Firefox Secure DNS, Chrome, Brave) and common VPN/proxy services. Daily-updated list, ~600 entries. Activate from the AdGuard > Filters page.",
    anti_bypass_hagezi_link:
      "Activate from the AdGuard page (feed slug: hagezi-doh-vpn).",
    anti_bypass_footer:
      "The Slate's own DoT towards its upstream resolvers is not affected (OUTPUT traffic, not FORWARD).",
    anti_bypass_enable: "Enable bypass prevention",
  },

  radio: {
    title: "Live radio state on the Slate",
    col_slot: "Slot",
    col_24: "2.4 GHz",
    col_5: "5 GHz",
    col_6: "6 GHz",
    col_mlo: "Multi-Link Operation",
    mlo_caption: "{ifname} bound to {ssid} ({state})",
    state_enabled: "enabled",
    state_disabled: "disabled",
    none: "—",
  },

  tailscale: {
    title: "Tailscale",
    subtitle:
      "Mesh VPN — remote administration channel and access to the home LAN from the Slate while on the road.",
    section_connection: "Connection",
    section_network_test: "Network test",
    state_label: "State",
  },

  proton: {
    title: "Proton VPN",
    subtitle: "WireGuard tunnel via Proton VPN",
    description:
      "Manages the WireGuard tunnel provided by Proton VPN: server selection, kill switch enforcement and connection state supervision.",
  },

  wifi: {
    title: "SSIDs",
    subtitle: "Wi-Fi catalog · {n} SSID",
    subtitle_plural: "Wi-Fi catalog · {n} SSIDs",
    description:
      "Lists the SSIDs managed by the controller, their bound network, their security and their broadcast state.",
    new: "New SSID",
  },

  firewall: {
    title: "Firewall",
    subtitle: "Zones, custom rules and audit trail",
    description:
      "UCI zones, rules injected by the controller (SC_FR_* prefix) and traceability of actions.",
    section_zones: "Zones",
  },

  vulnerabilities: {
    title: "Vulnerabilities",
    subtitle:
      "opkg Bill of Materials confronted with OSV and CVE2CAPEC. Identifies applicable CVEs and reconstructs attack paths.",
  },

  air_watch: {
    title: "Air Watch surveillance",
    subtitle:
      "Passive radio audit: evil twin detection, deauthentication frames, WPS activity and strong co-channel neighbours.",
  },

  anti_theft: {
    title: "Anti-theft",
    subtitle:
      "Autonomous defence mode: PIN lockout with escalation, threshold-based wipe, alerts dispatched from the Slate to the controller via webhooks.",
  },

  set_agent: {
    subtitle: "Local Slate agent",
    title: "Agent",
    description:
      "Deployment and supervision of the slate-ctrl agent: JSON profiles, offline application, physical-button integration.",
  },
  set_communication: {
    subtitle: "Communication",
    title: "Messages",
    description:
      "Toggles on-screen messages and sends a test message on demand.",
  },
  set_connectivity: {
    subtitle: "Controller connectivity",
    title: "Callback URLs",
    description:
      "URLs exposed by the controller and consumed by the Slate-side webhooks (touchscreen watcher, anti-theft).",
  },
  set_controller_https: {
    subtitle: "Controller HTTPS",
    title: "HTTPS",
    description:
      "Exposes the controller in HTTPS on the tailnet through Tailscale Serve. Automatic Let's Encrypt certificate.",
  },
  set_internal_ca: {
    subtitle: "Internal certificate authority",
    title: "Certificate authority",
    description:
      "Local root CA and certificate valid for 192.168.8.1, useful in offline hotel environments.",
  },
  set_setup_status: {
    subtitle: "Setup status",
    title: "Configuration",
    description:
      "Aggregated deployment view: Tailscale, certificate authority, SSH key, Slate, callbacks.",
  },
  set_ssh_key: {
    subtitle: "SSH key pair",
    title: "SSH key",
    description:
      "Key-only authentication on the Slate. Public-key generation and deployment in one click.",
  },
  set_tailnet_admin: {
    subtitle: "Tailnet admin peers",
    title: "Tailnet administration",
    description:
      "Allow-list of Tailscale peers permitted to reach the administration interfaces. Drives the admin_only flag on profiles.",
  },

  net_interfaces: {
    title: "Interfaces",
    subtitle:
      "Live state of the Slate's physical and logical interfaces (Ethernet, bridges, VLANs, VPN).",
  },
  net_diagnostic: {
    title: "Diagnostic",
    subtitle:
      "Network diagnostic toolbox: ping, traceroute, DNS lookup and throughput measurement.",
  },
  net_ambient: {
    title: "Radio watch",
    subtitle:
      "Continuous Wi-Fi spectrum monitoring (APScheduler job per band). Slow detection, low bandwidth cost.",
  },
  net_surveillance: {
    title: "Surveillance sessions",
    subtitle:
      "Named sessions with classified timeline of observed access points, contextual notes and per-session metadata.",
    new_session: "New session",
  },
  net_radio_history: {
    title: "Radio history",
    subtitle:
      "History of scheduled and manual scans. Useful to compare RF configurations over time.",
  },
  net_radio_map: {
    title: "Radio map",
    subtitle:
      "Geographic visualisation of RF discoveries linked to the Slate (Leaflet, local data).",
  },

  tailscale_audit: {
    title: "Tailscale audit",
    subtitle:
      "Local Slate posture audit via SSH and tailnet policy retrieved through the admin API (PAT required).",
  },

  remote: {
    title: "Remote control",
    subtitle: "Remote touchscreen",
    description:
      "Periodic screenshot of the Slate touchscreen and ability to push messages to the on-site operator.",
  },

  profile_form: {
    title_new: "New profile",
    title_edit: "Edit profile",
    subtitle:
      "Contextual configuration: SSIDs, VPN, DNS, filtering level, wallpaper and dedicated rules.",
  },

  wifi_orphans: {
    title: "Orphan SSIDs",
    subtitle:
      "Wireless sections present on the Slate but not referenced by the controller catalog. Useful to clean up leftover provisioning or SSH-side experiments.",
  },

  tor: {
    title_audit: "Tor audit",
    audit_subtitle:
      "Daemon state, active bridges, current circuits and exit quality. Validates DNS non-leakage.",
    title_networks: "Tor",
    networks_subtitle:
      "Per-network Tor routing: transparent for all traffic or SOCKS-only. Optional DNS-over-Tor and kill switch.",
  },

  login: {
    page_label: "Controller authentication",
    page_title: "Sign in",
    username: "Username",
    password: "Password",
    submit: "Sign in",
    connecting: "Signing in…",
    err_unauthorized: "Invalid credentials",
    err_network: "Controller unreachable",
  },

  adguard: {
    title: "AdGuard",
    subtitle: "Protection · AdGuard Home",
    description: "DNS filtering, blocklists and runtime statistics.",
  },

  security: {
    hub_title: "Security audit",
    hub_subtitle:
      "Aggregated indicators: hardening, vulnerabilities, VPN mesh quality.",
    reliability_label: "Slate reliability",
    hardening_title: "Device hardening",
    hardening_subtitle:
      "Slate hardening posture. OpenWrt and GL.iNet checks evaluated against a weighted baseline.",
    vulnerabilities_title: "Vulnerabilities",
    air_watch_title: "Air Watch surveillance",
    anti_theft_title: "Anti-theft",
  },

  networks: {
    title: "Networks",
    subtitle: "Bridges, VLANs and subnets referenced by SSIDs",
    counter: "Catalog · {n} bridge(s)",
    new: "New network",
    empty: "No network defined.",
  },

  profiles: {
    title: "Profiles",
    subtitle: "Contextual profiles",
    counter:
      "{n} profile(s) · \"template\" refers to shipped profiles, \"user\" to those created by the operator.",
    new: "New profile",
    regenerate_all: "Regenerate wallpapers",
    regenerate_all_title:
      "Regenerates the \"home\" and \"lock\" wallpapers of every profile using the active theme. Existing files are overwritten.",
    regenerating: "Generating…",
    regen_success: "{n} wallpaper(s) regenerated",
    regen_pushed_active: "Active profile {name} updated on the Slate (home + lock, gl_screen restarted)",
    regen_pushed_active_failed: "Push to Slate failed: {errors}",
    regen_no_active:
      "No active profile — activate a profile to push the wallpapers to the Slate.",
    regen_partial_failures: "{n} failure(s):",
    no_profiles: "No profile defined yet. Click \"New profile\".",
  },

  devices: {
    title: "Devices",
    subtitle:
      "GL.iNet hardware driven by the controller · adoption and hardening",
    counter: "Managed devices · {n}",
    new: "New device",
    empty:
      "No device declared yet. Click \"New device\" to start the adoption flow.",
    form_slug: "Identifier",
    form_label: "Label",
    form_admin_url: "Administration URL",
    form_slug_placeholder: "slate-mobile",
    form_label_placeholder: "Backpack Slate",
    form_admin_url_placeholder: "192.168.8.1",
    action_edit_urls: "Edit the list of administration URLs (LAN, Tailscale, custom)",
    action_reset_status: "Resets the local status to \"pending\". The Slate configuration is left untouched.",
    action_factory_reset: "Destructive action: triggers a firstboot and reboot of the Slate.",
    action_create: "Create",
    action_cancel: "Cancel",
    action_adopt: "Adopt",
  },

  dashboard: {
    title: "Dashboard",
    subtitle: "Real-time monitoring",
    refresh: "Refresh",
    syncing: "Syncing…",
    connecting: "Connecting to the controller…",
    error_unreachable: "Slate unreachable. {error}",
    status_online: "Online",
    status_offline: "Offline",
    label_lan: "LAN",
    label_mac: "MAC",
    label_wan: "WAN",
    label_country: "Country",
    wan_online: "Connected",
    wan_offline: "Disconnected",
    stat_uptime: "Uptime",
    stat_clients: "Clients",
    stat_cpu_temp: "CPU temperature",
    stat_cpu_cores: "{n} cores",
    stat_load_1m: "Load 1 min",
    stat_load_hint: "5 min {l5} · 15 min {l15}",
    stat_ram: "Memory",
    stat_ram_hint: "{value} free",
    services_title: "Active services",
    snapshot: "Snapshot at {time}",
    uptime_days: "{d} d {h} h {m} min",
    uptime_hours: "{h} h {m} min",
    uptime_minutes: "{m} min",
  },

  nav: {
    brand: "Controller",
    loading: "Loading…",
    logout: "Sign out",
    user: "User",
    section_network: "Network",
    section_air_wave: "Air Wave",
    section_audit: "Audit",
    section_vpn: "VPN",
    section_protection: "Protection",
    section_settings: "Settings",
    expand: "Expand",
    collapse: "Collapse",
    reliability_tooltip: "Slate reliability: {percent}% — {label}",
    reliability_unknown: "Slate reliability: unknown",
    item_dashboard: "Dashboard",
    item_devices: "Devices",
    item_profiles: "Profiles",
    item_remote_control: "Remote control",
    item_interfaces: "Interfaces",
    item_diagnostic: "Diagnostic",
    item_networks: "Networks",
    item_ssids: "SSIDs",
    item_ssids_orphans: "Orphan SSIDs",
    item_tor: "Tor",
    item_rf_scanner: "RF scanner",
    item_geo_map: "Geographic map",
    item_ambient: "Radio watch",
    item_surveillance: "Surveillance",
    item_pcap: "PCAP capture",
    item_hardening: "Hardening",
    item_vulnerabilities: "Vulnerabilities",
    item_tailscale_audit: "Tailscale audit",
    item_tor_audit: "Tor audit",
    item_air_watch: "Air Watch surveillance",
    item_anti_theft: "Anti-theft",
    item_proton_vpn: "Proton VPN",
    item_fortinet: "Fortinet — configuration",
    item_fortinet_connect: "Fortinet — connect",
    item_tailscale: "Tailscale",
    item_adguard: "AdGuard",
    item_dns: "DNS",
    item_firewall: "Firewall",
    item_setup_status: "Setup status",
    item_ssh_keypair: "SSH key pair",
    item_https_controller: "Controller HTTPS",
    item_internal_ca: "Internal certificate authority",
    item_tailnet_admin: "Tailnet admin peers",
    item_callback_urls: "Callback URLs",
    item_communication: "Communication",
    item_local_agent: "Local agent",
    item_appearance: "Appearance",
  },

  settings: {
    title: "Settings",
    subtitle: "Global controller configuration",
    subsection_count: "{n} subsection",
    subsection_count_plural: "{n} subsections",
    nav_appearance: "Appearance",
    nav_communication: "Communication",
    nav_connectivity: "Connectivity",
    nav_agent: "Local agent",
    nav_ssh: "SSH key",
    nav_tailnet: "Tailnet administration",
    nav_https: "Controller HTTPS",
    nav_ca: "Internal certificate authority",
    nav_setup: "Setup status",
    hub: {
      setup_title: "Setup status",
      setup_desc:
        "Aggregated view of the deployment state (Tailscale, certificate authority, SSH key, Slate, callbacks). Acts as a post-installation checklist.",
      ssh_title: "SSH key pair",
      ssh_desc:
        "Key-only authentication on the Slate. Generation and deployment of the public key in one click.",
      https_title: "Controller HTTPS",
      https_desc:
        "Exposes the controller over HTTPS on the tailnet through Tailscale Serve. Automatic Let's Encrypt certificate, never publicly reachable.",
      ca_title: "Internal certificate authority",
      ca_desc:
        "Local root CA and certificate valid for the Slate on 192.168.8.1, useful in offline hotel environments. One-time install of the Root CA removes browser certificate warnings.",
      tailnet_title: "Tailnet admin peers",
      tailnet_desc:
        "Allow-list of Tailscale peers permitted to reach the administration interfaces (LuCI, SSH, AdGuard, controller). Drives the admin_only flag on profiles.",
      communication_title: "Communication",
      communication_desc:
        "Toggles on-screen messages and sends a test message on demand.",
      agent_title: "Local agent",
      agent_desc:
        "Deploys the slate-ctrl agent on the Slate: JSON-formatted profiles, offline application, physical-button integration.",
    },
  },
};

export const DICT: Record<Lang, DictTree> = {
  fr: FR,
  en: EN,
};
