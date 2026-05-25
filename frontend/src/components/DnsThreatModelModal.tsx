import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  Eye,
  Flag,
  Globe,
  HeartHandshake,
  Lock,
  Network,
  Shield,
  ShieldAlert,
  ShieldOff,
  Skull,
  Wifi,
  XCircle,
} from "lucide-react";

import { getAntiBypassStatus, listProtections } from "@/api/dns";
import ThreatModelModal, {
  ChainPoint,
  Disclaimer,
  Mitigation,
  Recommendation,
  Section,
  StatusItem,
  Step,
  Threat,
  type ThreatTab,
} from "@/components/ThreatModelModal";

/**
 * DNS-specific threat model modal. Uses the generic <ThreatModelModal/>
 * wrapper and supplies four tabs of content: Pipeline, Threats, Mitigations,
 * Status. Voice is impersonal ("L'utilisateur", "Le routeur") since the
 * module ships to a public community of operators.
 */
export default function DnsThreatModelModal({
  onClose,
}: {
  onClose: () => void;
}) {
  const [tab, setTab] = useState("pipeline");

  const tabs: ThreatTab[] = [
    { key: "pipeline", label: "Comment le DNS fonctionne", icon: Network, content: <TabPipeline /> },
    { key: "threats", label: "Attaques connues", icon: Skull, content: <TabThreats /> },
    { key: "mitigations", label: "Protections", icon: Shield, content: <TabMitigations /> },
    { key: "status", label: "État courant", icon: Eye, content: <TabStatus /> },
  ];

  return (
    <ThreatModelModal
      title="Modèle de menace DNS"
      subtitle="Pourquoi le DNS est l'un des points les plus attaqués d'Internet, et ce que chaque protection apporte."
      tabs={tabs}
      activeTab={tab}
      onTabChange={setTab}
      onClose={onClose}
    />
  );
}

// ---------------------------- TAB 1 : PIPELINE ---------------------------- //

function TabPipeline() {
  return (
    <div className="space-y-6">
      <Section
        title="Pourquoi commencer par comprendre le pipeline"
        intro="Le DNS (Domain Name System) traduit les noms (exemple.com) en adresses IP. C'est un service fondamental — sans lui, rien ne marche. Mais il a été conçu en 1983, à une époque où la sécurité n'était pas la priorité. Aujourd'hui, une simple requête traverse 4 à 5 systèmes différents avant d'obtenir une réponse. Chacun de ces 'hops' est une cible potentielle. Comprendre la chaîne complète permet de placer les bonnes défenses aux bons endroits."
      />

      <div className="rounded-lg border border-[color:var(--color-cyber-border-strong)] bg-[color:var(--color-cyber-bg)] p-4 font-mono text-xs">
        <Step
          icon={<Wifi className="h-4 w-4 text-cyan-400" />}
          label="1. Le client (navigateur, application, objet connecté)"
          arrow="UDP/53 en clair  —  l'application envoie sa demande au resolver configuré"
          color="text-[color:var(--color-cyber-fg)]"
        />
        <Step
          icon={<Network className="h-4 w-4 text-emerald-400" />}
          label="2. dnsmasq (le résolveur local du Slate)"
          arrow="UDP/53 en boucle locale  —  redirige vers AdGuard"
          color="text-emerald-300"
        />
        <Step
          icon={<Shield className="h-4 w-4 text-emerald-400" />}
          label="3. AdGuard Home (filtre les blocklists, choisit l'upstream selon le réseau)"
          arrow="TLS/853 chiffré (DoT) ou HTTPS/443 (DoH)  —  canal sécurisé vers Internet"
          color="text-emerald-300"
        />
        <Step
          icon={<Globe className="h-4 w-4 text-blue-400" />}
          label="4. Le résolveur public (DNS4EU, Quad9, Cloudflare, dns0.eu...)"
          arrow="Requêtes vers les serveurs autoritaires de chaque zone"
          color="text-blue-300"
        />
        <Step
          icon={<Lock className="h-4 w-4 text-purple-400" />}
          label="5. Les serveurs autoritaires (propriétaires de chaque domaine)"
          arrow="La réponse remonte toute la chaîne, idéalement signée cryptographiquement (DNSSEC)"
          color="text-purple-300"
          last
        />
      </div>

      <Section
        title="Les 3 zones d'exposition"
        intro="Chaque tronçon de la chaîne expose à des menaces différentes. Une protection donnée ne couvre généralement qu'une zone — il faut composer pour avoir une défense en profondeur."
      />

      <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
        <ChainPoint
          label="Zone 1 — Client vers Slate"
          icon={<Wifi className="h-4 w-4" />}
          color="cyan"
          threats={[
            "Sniffing du WiFi (ouvert ou WPA2 cassé)",
            "Faux point d'accès (evil twin)",
            "ARP spoofing sur le LAN",
            "Application qui ignore le DNS DHCP",
          ]}
          mitigation="WiFi sécurisé (WPA3) + redirection NAT forcée de toute requête port 53 vers le résolveur du routeur"
        />
        <ChainPoint
          label="Zone 2 — Slate vers résolveur public"
          icon={<Globe className="h-4 w-4" />}
          color="emerald"
          threats={[
            "Interception par le FAI",
            "Captive portal hostile (hôtels, aéroports)",
            "Censure étatique du DNS",
            "MITM sur WiFi public",
          ]}
          mitigation="DoT (port 853) ou DoH (port 443) — la requête voyage chiffrée bout-à-bout"
        />
        <ChainPoint
          label="Zone 3 — Résolveur vers serveurs autoritaires"
          icon={<Lock className="h-4 w-4" />}
          color="purple"
          threats={[
            "Détournement BGP (route hijack)",
            "Empoisonnement du cache résolveur",
            "Compromission du résolveur public",
            "Faille à la racine ou au registry",
          ]}
          mitigation="DNSSEC — chaque réponse est signée par le propriétaire de la zone, vérifiable indépendamment"
        />
      </div>

      <Disclaimer>
        Le pipeline ci-dessus correspond à la configuration recommandée (résolveur
        local + filtrage + DoT/DoH). Sur un routeur sans protection, les étapes 2
        et 3 sont remplacées par un appel direct du client vers le DNS du FAI, en
        clair, sur le port 53 — c'est la configuration par défaut de la majorité
        des box résidentielles dans le monde en 2026.
      </Disclaimer>
    </div>
  );
}

// ---------------------------- TAB 2 : THREATS ---------------------------- //

function TabThreats() {
  return (
    <div>
      <Section
        title="Attaques connues, avec cas réels documentés"
        intro="Le DNS est l'un des protocoles les plus attaqués d'Internet, parce qu'un seul mensonge bien placé suffit à rediriger un utilisateur vers n'importe quoi. Voici les 7 grandes familles d'attaques, avec des incidents historiques pour chacune — pas de théorie, des faits."
      />

      <Threat
        icon={<Wifi className="h-5 w-5" />}
        severity="high"
        title="Sniffing WiFi et faux point d'accès (evil twin)"
        scenario="Un utilisateur se connecte au WiFi gratuit d'un café, d'un aéroport ou d'un hôtel. Un attaquant à côté capture le trafic (WiFi ouvert) ou a monté son propre point d'accès avec le même SSID. Toutes les requêtes DNS en clair sont visibles : sites bancaires, services pro, recherches sensibles. L'attaquant peut aussi injecter de fausses réponses pour rediriger vers du phishing."
        impact="Fuite du profil de navigation complet. Possibilité de redirection silencieuse vers des sites malveillants imitant les vrais (banque, webmail)."
        defense="DoT/DoH chiffre la requête. L'attaquant voit uniquement 'connexion TLS vers 9.9.9.9:853', pas le contenu. Cas réel — Black Hat 2017 : démonstration publique d'evil twin automatisé (outil Wifiphisher) capable de fishing transparent en 30 secondes sur des WiFi ouverts. Defcon 2019 : campagne anti-CCC sur le DEF CON 27 démontre le vol de credentials Google via DNS spoofing sur WiFi ouvert."
        defenseActive
      />

      <Threat
        icon={<Network className="h-5 w-5" />}
        severity="high"
        title="Interception et redirection forcée par le FAI ou un État"
        scenario="Le FAI ou un opérateur étatique intercepte le port 53 sortant et réécrit les réponses. Objectifs possibles : censure (bloquer des sites), publicité (rediriger les NXDOMAIN vers des pages d'annonces), surveillance, ou conformité réglementaire."
        impact="Censure invisible, profilage commercial, redirection vers serveurs de surveillance."
        defense="DoT/DoH empêche l'interception sans casser TLS (ce qui déclencherait une alerte certificat). Cas réels documentés : (1) Turquie 20 mars 2014 — le gouvernement Erdogan force tous les FAI à rediriger les requêtes vers Twitter ; les utilisateurs basculent vers 8.8.8.8, puis le gouvernement bloque aussi 8.8.8.8 et 8.8.4.4 quelques jours plus tard. (2) Iran 2022-2025 — blocage systématique des résolveurs étrangers, force l'usage des DNS d'État. (3) Russie 2022+ — Roskomnadzor exige des FAI qu'ils filtrent au niveau DNS via la blocklist Réestr. (4) Brésil 2023 — STF ordonne le blocage de Telegram via injonctions DNS aux FAI. (5) France 2024-2025 — multiplication des blocages DNS HADOPI/Arcom contre les sites de streaming illégal."
        defenseActive
      />

      <Threat
        icon={<ShieldAlert className="h-5 w-5" />}
        severity="critical"
        title="Détournement BGP du résolveur public"
        scenario="Un système autonome (AS) malveillant ou compromis annonce sur Internet qu'il détient les adresses IP d'un résolveur DNS public. Le trafic des utilisateurs est silencieusement détourné vers l'attaquant, qui répond avec ce qu'il veut. Le résolveur légitime n'est même pas informé."
        impact="Attaque à grande échelle, invisible aux victimes, qui touche potentiellement des millions d'utilisateurs simultanément. Toutes les requêtes DNS sont sous contrôle de l'attaquant le temps que la propagation BGP soit annulée."
        defense="DNSSEC en validation locale. Les réponses falsifiées par l'attaquant n'ont pas la signature cryptographique valide → le résolveur local renvoie SERVFAIL, l'utilisateur sait que quelque chose cloche. Cas réels documentés : (1) 24 février 2008 — Pakistan Telecom annonce accidentellement les préfixes de YouTube, mettant le site hors ligne mondialement pendant 2 heures. (2) 24 avril 2018 — détournement BGP de Route 53 d'Amazon, redirige le trafic de MyEtherWallet vers un faux site, vol d'environ 152 000 USD en Ethereum en 2 heures. (3) 1er avril 2020 — Rostelecom (Russie) détourne 200+ préfixes incluant Akamai, Cloudflare, Hetzner, Digital Ocean, Amazon ; durée 1 heure, impact mondial. (4) Juin 2019 — China Telecom redirige le trafic européen via l'Asie pendant 2 heures, soupçon d'interception. (5) Mars 2022 — Twitter inaccessible mondialement suite à un mauvais BGP de RTComm.ru."
        defenseActive={false}
      />

      <Threat
        icon={<Skull className="h-5 w-5" />}
        severity="high"
        title="Empoisonnement du cache résolveur (Kaminsky-style)"
        scenario="Un attaquant exploite la prédictibilité de l'identifiant de transaction DNS et le timing pour injecter de fausses réponses dans le cache d'un résolveur public. Une fois empoisonné, le cache sert des IP malveillantes à tous les clients de ce résolveur, pendant le TTL des entrées (souvent plusieurs heures)."
        impact="Tous les utilisateurs du résolveur compromis voient les fausses réponses, sans aucun moyen de détection côté client si DNSSEC n'est pas validé."
        defense="DNSSEC bloque les réponses non signées au niveau cryptographique. Cas réels documentés : (1) Juillet-août 2008 — Dan Kaminsky démontre à Black Hat USA une faille générique qui rend l'empoisonnement triviale en quelques secondes. Patch coordonné mondial déployé en urgence (CVE-2008-1447). (2) 2008-2011 — campagne DNSChanger d'EstDomains/Rove Digital, ~4 millions de PC infectés, modifient les paramètres DNS vers serveurs malveillants. FBI Operation Ghost Click démantèle en novembre 2011. (3) Septembre 2020 — chercheurs UC Riverside publient SAD DNS (CVE-2020-25705), faille side-channel qui réintroduit le risque Kaminsky sur les implémentations modernes. (4) Janvier 2021 — DNSpooq (JSOF), 7 CVE majeures dans dnsmasq, touche des millions de routeurs et IoT."
        defenseActive={false}
      />

      <Threat
        icon={<Flag className="h-5 w-5" />}
        severity="medium"
        title="Compromission du résolveur public ou contrainte légale"
        scenario="Le résolveur DNS public utilisé (même un acteur de bonne réputation) peut être piraté, recevoir une injonction légale, voir un employé interne agir mal, ou être contraint par un changement réglementaire de mentir sur certaines réponses. Sans validation indépendante côté client, aucun moyen de le détecter."
        impact="Backdoor invisible et ciblée : pour certains domaines stratégiques uniquement, l'utilisateur arrive sur un proxy de surveillance qui ressemble parfaitement au site original."
        defense="DNSSEC en validation locale détecte les signatures invalides. Choix d'un résolveur en juridiction protectrice (UE, Suisse) limite le risque légal. Cas réels documentés : (1) Avril 2019 — Sea Turtle (rapport Cisco Talos), opération APT iranienne soupçonnée, compromet des registries DNS de pays cibles pour intercepter les communications gouvernementales pendant 2 ans. (2) Novembre 2018 — DNSpionage (rapport FireEye/Mandiant) cible Liban, ÉAU et Moyen-Orient, hijack DNS sur infrastructures gouvernementales. (3) Janvier 2019 — CISA Emergency Directive 19-01 ordonne aux agences fédérales américaines de vérifier l'intégrité de tous leurs records DNS suite à une vague d'attaques DNSpionage."
        defenseActive={false}
      />

      <Threat
        icon={<Eye className="h-5 w-5" />}
        severity="high"
        title="Contournement DoH/DoT par le client (Firefox, Chrome, applications)"
        scenario="Depuis 2020, les navigateurs modernes activent le DNS chiffré (DoH/DoT) directement vers leurs propres résolveurs, en ignorant celui du routeur. Le résolveur local et ses filtres ne voient plus rien. Le trafic est camouflé dans du HTTPS standard, donc invisible à l'inspection réseau."
        impact="Les filtres parental, blocklists malware, contrôles 'famille' du routeur deviennent inopérants. Un mineur sur un réseau 'protégé' peut ouvrir Firefox et atteindre n'importe quoi. Les filtres antivirus DNS sont bypassés."
        defense="Bloquer le port TCP/853 sortant du LAN au pare-feu (force le fallback sur DNS système). Ajouter une blocklist des endpoints DoH publics dans le résolveur local (~600 entrées maintenues par HaGeZi). Cas et changements documentés : (1) Février 2020 — Mozilla active DoH par défaut pour tous les utilisateurs Firefox américains, sans opt-in explicite. Vague de protestations des admins entreprise. (2) Mai 2020 — Chrome 83 ajoute Secure DNS automatique. (3) 2021-2023 — la majorité des familles découvre que les contrôles parentaux DNS de leur Pi-hole/OpenDNS sont contournés par les enfants via Firefox. Pi-hole publie une blocklist anti-DoH (~80 entrées initiales, ~600 aujourd'hui)."
        defenseActive={false}
      />

      <Threat
        icon={<ShieldOff className="h-5 w-5" />}
        severity="medium"
        title="Application qui code en dur un serveur DNS"
        scenario="Une application (jeu mobile, télévision connectée, objet IoT, malware) ignore complètement le DNS distribué par DHCP et envoie ses requêtes directement vers une adresse codée en dur (8.8.8.8, 114.114.114.114, ou un serveur de l'éditeur). Bypass total du résolveur local et de ses filtres."
        impact="L'application peut joindre ses serveurs de contrôle sans filtrage, collecter de la télémétrie inavouable, ou communiquer avec un C2 malveillant sans alerter le réseau."
        defense="Règle NAT iptables qui réécrit silencieusement toute requête sur le port 53 (UDP et TCP) vers le résolveur local du Slate. Active par défaut sur les routeurs GL.iNet via la chaîne adg_redirect. Cas réels documentés : (1) Smart TV Samsung 2015 — Vizio collecte les habitudes de visionnage et les envoie en DNS plain, révélé par Pro Publica. (2) Roomba 2017 — iRobot envoie les plans des maisons via DNS hardcodés. (3) TikTok Android 2020 — analyse réseau Penn State montre des requêtes DNS forcées vers serveurs ByteDance contournant les paramètres système. (4) Multiples malwares Mirai (2016+) — utilisent leurs propres résolveurs pour échapper aux DNS sinkholes."
        defenseActive
      />
    </div>
  );
}

// ---------------------------- TAB 3 : MITIGATIONS ---------------------------- //

function TabMitigations() {
  return (
    <div>
      <Section
        title="Ce que chaque mécanisme apporte concrètement"
        intro="Aucun mécanisme ne couvre toutes les menaces. La sécurité DNS se construit par couches successives — DoT/DoH pour le transport, DNSSEC pour l'authenticité, filtrage pour le contenu, redirection NAT pour la cohérence. Voici la grille complète."
      />

      <Mitigation
        icon={<Lock className="h-5 w-5 text-emerald-400" />}
        name="DoT — DNS over TLS (port 853)"
        what="Chiffre la requête DNS dans une session TLS classique, comme HTTPS mais sur un port dédié. Le résolveur prouve son identité avec un certificat X.509, l'utilisateur vérifie le hostname. Standard IETF RFC 7858, déployé depuis 2016."
        protects={[
          "Sniffing WiFi public",
          "Interception FAI",
          "Censure DNS par filtrage de port 53",
        ]}
        notProtects={[
          "Détournement BGP du résolveur",
          "Empoisonnement du cache résolveur",
          "Résolveur compromis ou contraint",
        ]}
        cost="Latence supplémentaire 5-20 ms par requête (handshake TLS amorti par keepalive). Tous les résolveurs publics du catalogue le supportent."
      />

      <Mitigation
        icon={<Lock className="h-5 w-5 text-blue-400" />}
        name="DoH — DNS over HTTPS (port 443)"
        what="Encapsule la requête DNS dans une requête HTTPS POST classique, indistinguable du trafic web. Standard IETF RFC 8484, déployé depuis 2018. Particulièrement utile contre les pare-feu qui bloquent le port 853."
        protects={[
          "Sniffing WiFi public",
          "Interception FAI",
          "Pare-feu national filtrant les ports DNS (Chine, Iran)",
          "Inspection profonde des paquets DNS",
        ]}
        notProtects={[
          "Détournement BGP",
          "Empoisonnement du cache",
          "Résolveur compromis",
          "Centralisation autour de quelques fournisseurs (paradoxe vie privée)",
        ]}
        cost="Latence 10-30 ms par requête (overhead HTTPS plus important que TLS pur). Inspection réseau impossible côté infrastructure (peut être un inconvénient en entreprise)."
      />

      <Mitigation
        icon={<ShieldAlert className="h-5 w-5 text-purple-400" />}
        name="DNSSEC — DNS Security Extensions"
        what="Ajoute des signatures cryptographiques aux réponses DNS, vérifiables avec une chaîne de confiance qui remonte jusqu'à la racine ICANN. Trois modèles existent : (a) confiance au résolveur amont — le résolveur public valide pour l'utilisateur, fonctionnement par défaut quand on choisit Quad9 ou DNS4EU ; (b) validation locale — le routeur vérifie lui-même chaque réponse, défense en profondeur indépendante du résolveur ; (c) stub validation — chaque application valide elle-même, rare en pratique."
        protects={[
          "Détournement BGP (si validation locale)",
          "Empoisonnement de cache (si validation locale)",
          "Résolveur public compromis (si validation locale)",
        ]}
        notProtects={[
          "Contenu lisible (DNSSEC signe, ne chiffre pas — il faut DoT/DoH pour cela)",
          "Domaines sans DNSSEC déployé (moins de 10% des .com en 2025, plus de 90% des .gov, .nl, .se)",
        ]}
        cost="Validation locale : 20-50 ms par requête (vérifications crypto). Risque résiduel : environ 0,5% des domaines ont un DNSSEC mal configuré → SERVFAIL → site inaccessible."
      />

      <Mitigation
        icon={<Shield className="h-5 w-5 text-cyan-400" />}
        name="AdGuard Home — résolveur filtrant"
        what="Résolveur local intermédiaire entre le client et le résolveur public. Trois apports majeurs : (a) filtre les domaines via des blocklists (malware, pubs, trackers, adult content) ; (b) route différemment selon le client source (matching par CIDR ou MAC), permettant des politiques distinctes par réseau ; (c) centralise les statistiques DNS du foyer pour audit."
        protects={[
          "Accès à des domaines malware ou phishing connus",
          "Publicités et trackers domain-level",
          "Contenu adulte sur réseaux famille",
          "Contournement par-réseau (chaque réseau peut avoir son propre upstream + filtres)",
        ]}
        notProtects={[
          "Client qui utilise son propre résolveur DoH (Firefox, Chrome modernes)",
          "Application qui code en dur un serveur DNS (mitigé séparément par le DNAT)",
        ]}
        cost="~187 Mo de RAM en fonctionnement standard avec 5 blocklists. Devient un point de défaillance unique du DNS du foyer si le daemon plante."
      />

      <Mitigation
        icon={<Network className="h-5 w-5 text-amber-400" />}
        name="Redirection NAT du port 53 (DNS hijack local)"
        what="Règle iptables au niveau du pare-feu qui réécrit silencieusement toute requête sortante sur le port 53 (UDP et TCP) vers le résolveur local du routeur, peu importe la destination originale. Empêche les applications qui codent en dur leur DNS de bypasser le résolveur configuré."
        protects={[
          "Application qui code en dur 8.8.8.8 ou 1.1.1.1",
          "Télévisions connectées avec DNS interne",
          "Objets IoT chinois avec résolveurs hardcodés",
          "Malwares de la famille Mirai et dérivés",
        ]}
        notProtects={[
          "DoT (port 853) car port différent",
          "DoH (port 443) car indistinguable du trafic HTTPS normal",
        ]}
        cost="Zéro coût additionnel — une seule règle NAT. Active par défaut sur les routeurs GL.iNet via la chaîne adg_redirect."
      />

      <Mitigation
        icon={<ShieldOff className="h-5 w-5 text-red-400" />}
        name="Blocage du port TCP/853 sortant LAN→WAN"
        what="Règle pare-feu qui rejette les connexions TCP sortantes vers le port 853 venant des réseaux internes. Force les clients qui font du DoT propre (navigateurs, certaines apps) à se rabattre sur le DNS système (donc le résolveur du routeur)."
        protects={[
          "Clients qui utilisent un DoT direct vers Cloudflare/Quad9/Google contournant le résolveur local",
        ]}
        notProtects={[
          "DoH (caché dans HTTPS, port 443)",
        ]}
        cost="Aucun. Casse le DoT côté client mais ces clients basculent automatiquement sur le DNS système (DHCP)."
      />

      <Mitigation
        icon={<XCircle className="h-5 w-5 text-red-400" />}
        name="Blocklist DoH publique (anti-bypass DoH)"
        what="Liste curée des endpoints DoH publics connus (cloudflare-dns.com, dns.google, mozilla.cloudflare-dns.com, dns.adguard-dns.com, etc.) injectée dans le résolveur local. Quand un navigateur fait sa résolution bootstrap pour son DoH, le résolveur retourne NXDOMAIN, le navigateur bascule automatiquement sur le DNS système."
        protects={[
          "Firefox DoH activé par défaut depuis 2020",
          "Chrome Secure DNS depuis 2020",
          "Brave, Edge, Vivaldi",
          "Applications mobiles avec DoH hardcodé",
        ]}
        notProtects={[
          "Applications qui codent en dur l'adresse IP du endpoint DoH (rare mais existe)",
          "Bypass par DoT (couvert par la mitigation TCP/853)",
        ]}
        cost="~10 Mo de RAM dans AdGuard. Liste HaGeZi DoH/VPN/Proxy maintenue quotidiennement, ~600 entrées."
      />
    </div>
  );
}

// ---------------------------- TAB 4 : STATUS ---------------------------- //

function TabStatus() {
  const protections = useQuery({
    queryKey: ["dns", "protections"],
    queryFn: listProtections,
  });
  const antiBypass = useQuery({
    queryKey: ["dns", "anti-bypass"],
    queryFn: getAntiBypassStatus,
  });
  const nbProt = protections.data?.protections.length ?? 0;
  const ab = antiBypass.data;
  const glActive = ab
    ? Object.values(ab.gl_rules_enabled).filter((v) => v).length
    : 0;
  const glTotal = ab
    ? Object.values(ab.gl_rules_enabled).filter((v) => v !== null).length
    : 0;

  return (
    <div>
      <Section
        title="État courant de la protection DNS"
        intro="Photographie instantanée de ce qui est actif sur le routeur, par couche. Les éléments en orange représentent la surface d'attaque résiduelle — pas forcément critique mais à connaître."
      />

      <div className="space-y-2">
        <StatusItem
          name="Redirection NAT du port 53 vers le résolveur local"
          active
          note="Chaîne adg_redirect active par défaut sur les routeurs GL.iNet — couvre les applications qui codent en dur un serveur DNS."
        />
        <StatusItem
          name="DoT/DoH côté Slate vers les résolveurs publics"
          active={nbProt > 0}
          note={
            nbProt > 0
              ? `${nbProt} réseau(x) configuré(s) avec une protection DNS chiffrée vers le résolveur public.`
              : "Aucun réseau ne dispose encore d'une protection DNS chiffrée. Configurer au moins un niveau de sécurité par réseau dans la page Réseaux."
          }
        />
        <StatusItem
          name="AdGuard Home — résolveur filtrant local + routage par-client"
          active
          note="Daemon actif. Chaque réseau peut être associé à un client AdGuard distinct (matching par CIDR) avec son propre upstream et ses propres filtres."
        />
        <StatusItem
          name="DNSSEC en validation locale (AdGuard enable_dnssec)"
          active={false}
          note="Désactivé. Le routeur fait confiance au résolveur public choisi (Quad9, DNS4EU, etc.) pour valider les signatures. Si ce résolveur est compromis ou subit un détournement BGP, les réponses falsifiées seraient acceptées sans alerte."
        />
        <StatusItem
          name="DNSSEC en validation locale (dnsmasq dnssec)"
          active={false}
          note="Option uci absente. Aucune vérification cryptographique côté routeur. Compléterait AdGuard en défense en profondeur."
        />
        <StatusItem
          name="Blocage du port TCP/853 sortant LAN→WAN (anti-bypass DoT)"
          active={ab?.custom_block_dot_active ?? false}
          note={
            ab?.custom_block_dot_active
              ? "Règle firewall slate_ctrl_block_dot_lan active — les clients qui tentent du DoT direct sont rejetés et fallback sur le DNS système."
              : "Aucune règle de blocage. Un client utilisant DoT directement vers un résolveur public peut contourner les filtres AdGuard. Activable depuis la section Anti-bypass de la page DNS."
          }
        />
        <StatusItem
          name="Règles anti-fuite DNS GL.iNet (drop_leaked_dns)"
          active={glActive > 0 && glActive === glTotal}
          note={
            glTotal === 0
              ? "Aucune règle drop_leaked détectée dans la config firewall."
              : `${glActive}/${glTotal} règles activées. Couvrent les fuites DNS sur tunnels WireGuard/OpenVPN et zones LAN/guest.`
          }
        />
        <StatusItem
          name="Blocklist DoH publique (anti-bypass DoH)"
          active={false}
          note="À vérifier manuellement dans AdGuard : le feed hagezi-doh-vpn doit être activé. Disponible depuis la page AdGuard > Filtres."
        />
      </div>

      <Recommendation
        title="Pour fermer les trous résiduels"
        items={[
          <>
            <strong>Activer la validation DNSSEC locale</strong> dans AdGuard
            (toggle global) — couvre les détournements BGP, l'empoisonnement de
            cache et la compromission de résolveur public.
          </>,
          <>
            <strong>Bloquer TCP/853 sortant LAN→WAN</strong> au pare-feu et
            ajouter la blocklist HaGeZi DoH/VPN dans AdGuard — ferme le bypass
            des navigateurs modernes (Firefox, Chrome).
          </>,
          <>
            <HeartHandshake className="inline h-3 w-3" />{" "}
            <strong>Composer plusieurs couches</strong> — DoT/DoH protège le
            transport, DNSSEC protège l'authenticité, AdGuard filtre le
            contenu, le DNAT couvre les bypass triviaux. Aucune couche n'est
            redondante.
          </>,
        ]}
      />

      <Disclaimer>
        Aucune protection DNS ne couvre l'intégralité du trafic moderne. Un
        utilisateur déterminé peut toujours installer un VPN qui chiffre tout
        son trafic indépendamment du DNS. Le but ici est de protéger
        l'utilisateur lambda et les applications qui suivent les bonnes
        pratiques, pas de construire un système inviolable.
      </Disclaimer>
    </div>
  );
}
