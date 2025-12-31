import streamlit as st
import pandas as pd
import numpy as np

# ==============================================================================
# PARTIE 1 : MOTEUR DE CALCUL SCIENTIFIQUE (BACKEND)
# Basé sur: Sonneveld & Voogt, "Plant Nutrition of Greenhouse Crops", Chap 12-13
# ==============================================================================

class VoogtNutrientAlgorithm:
    """
    Implémentation de l'Algorithme Universel de Calcul de Solutions Nutritives.
    Intègre les logiques de compensation (Feedback), d'équilibre ionique et de correction EC.
    """

    def __init__(self):
        # Valences pour le calcul de l'équilibre électrique (en meq)
        self.valences = {
            'K': 1, 'Ca': 2, 'Mg': 2, 'NH4': 1, 'Na': 1,  # Cations (+)
            'NO3': 1, 'SO4': 2, 'H2PO4': 1, 'Cl': 1, 'HCO3': 1 # Anions (-)
        }
        
        # Poids Moléculaires (mg/mmol) pour conversion éventuelle en poids d'engrais
        self.molar_weights = {
            'K': 39.1, 'Ca': 40.1, 'Mg': 24.3, 'NH4': 18.0, 
            'NO3': 62.0, 'SO4': 96.1, 'H2PO4': 97.0, 'Cl': 35.5, 'Na': 23.0
        }

    def calculate_drip_recipe(self, target_vals, analysis_vals, uptake_vals, water_vals, target_ec, correction_factor):
        """
        Exécute la boucle de calcul complète.
        """
        # Liste des éléments nutritifs majeurs gérés par l'algorithme
        elements = ['NO3', 'H2PO4', 'SO4', 'K', 'Ca', 'Mg', 'NH4']
        warnings = []
        
        # --- ÉTAPE A : CALCUL DE L'AJUSTEMENT (FEEDBACK) ---
        # Formule : Besoin = Absorption + (Cible - Analyse) * Facteur
        adjusted_vals = {}
        
        for el in elements:
            # Récupération sécurisée des valeurs (0.0 par défaut)
            target = target_vals.get(el, 0.0)
            analysis = analysis_vals.get(el, 0.0)
            uptake = uptake_vals.get(el, 0.0)
            
            gap = target - analysis
            
            # HEURISTIQUE DE SÉCURITÉ 1 : Plafonnement de la correction
            # Empêche des changements drastiques si l'analyse est aberrante
            max_correction = target * 0.6 # Max 60% de la cible en correction
            correction_term = gap * correction_factor
            
            if abs(correction_term) > max_correction:
                limit = max_correction if gap > 0 else -max_correction
                correction_term = limit
                warnings.append(f"⚠️ {el} : Correction plafonnée (Écart Analyse/Cible trop grand).")

            # Calcul de base avant équilibrage
            base_calc = uptake + correction_term
            
            # HEURISTIQUE 2 : Pas de concentration négative
            if base_calc < 0:
                base_calc = 0
                warnings.append(f"📉 {el} : Stock substrat critique (Excès). Apport coupé temporairement.")
            
            adjusted_vals[el] = base_calc

        # --- ÉTAPE B : ÉQUILIBRAGE IONIQUE (NEUTRALITÉ ÉLECTRIQUE) ---
        # Calcul des sommes de charges (milli-équivalents)
        sum_cations = sum(adjusted_vals[el] * self.valences[el] for el in ['K', 'Ca', 'Mg', 'NH4'] if el in adjusted_vals)
        sum_anions = sum(adjusted_vals[el] * self.valences[el] for el in ['NO3', 'SO4', 'H2PO4'] if el in adjusted_vals)
        
        imbalance = sum_cations - sum_anions
        
        # Correction de l'équilibre
        if imbalance > 0.1: 
            # Manque d'Anions -> On ajoute NO3 (le plus mobile et assimilable)
            add_no3 = imbalance / self.valences['NO3']
            adjusted_vals['NO3'] += add_no3
            warnings.append(f"⚖️ Équilibrage : Ajout de NO3 (+{add_no3:.2f} mmol/L) pour compenser les Cations.")
            
        elif imbalance < -0.1:
            # Manque de Cations -> On ajoute un mix K/Ca
            missing = abs(imbalance)
            # Répartition 50/50 pour ne pas déséquilibrer l'antagonisme K/Ca
            adjusted_vals['K'] += (missing * 0.5) / self.valences['K']
            adjusted_vals['Ca'] += (missing * 0.5) / self.valences['Ca']
            warnings.append(f"⚖️ Équilibrage : Ajout K/Ca (+{missing:.2f} meq) pour compenser les Anions.")

        # --- ÉTAPE C : CORRECTION EC (DENSITÉ) ---
        # Estimation de l'EC actuelle (Empirique : Somme Cations meq / 10)
        current_meq = sum(adjusted_vals[el] * self.valences[el] for el in ['K', 'Ca', 'Mg', 'NH4'])
        if current_meq == 0: current_meq = 0.1 # Eviter div/0
        
        estimated_ec = current_meq / 10.0 
        
        # Facteur multiplicateur pour atteindre l'EC cible
        if estimated_ec < 0.2: estimated_ec = 0.2 # Sécurité
        ec_ratio = target_ec / estimated_ec
        
        final_drip_conc = {}
        for el in elements:
            # Certains éléments ne sont pas "scalés" avec l'EC (NH4 pour le pH, H2PO4 souvent fixe)
            if el in ['NH4', 'H2PO4']: 
                final_drip_conc[el] = adjusted_vals[el] 
            else:
                final_drip_conc[el] = adjusted_vals[el] * ec_ratio

        # --- ÉTAPE D : SOUSTRACTION EAU BRUTE ---
        fertilizer_needs = {}
        for el in elements:
            water_content = water_vals.get(el, 0.0)
            need = final_drip_conc[el] - water_content
            
            if need < 0:
                need = 0
                warnings.append(f"🚨 {el} : L'eau de source apporte déjà trop ({water_content} > {final_drip_conc[el]:.2f}).")
            
            fertilizer_needs[el] = need

        # --- FORMATAGE POUR L'AFFICHAGE ---
        # Création d'un DataFrame récapitulatif
        df_results = pd.DataFrame({
            'Cible (Target)': target_vals,
            'Analyse (Labo)': analysis_vals,
            'Ajusté (Feedback)': adjusted_vals,
            'Sol. Goutteur (Brut)': final_drip_conc,
            'Eau Source': water_vals,
            'Besoin Net (Engrais)': fertilizer_needs
        })
        
        return df_results.round(2), warnings, final_drip_conc

# ==============================================================================
# PARTIE 2 : DONNÉES DE RÉFÉRENCE (ANNEXE C SONNEVELD)
# ==============================================================================

CROP_PROFILES = {
    "Tomate (Standard)": {
        "targets": {'NO3': 23.0, 'H2PO4': 1.0, 'SO4': 6.8, 'K': 8.0, 'Ca': 10.0, 'Mg': 4.5, 'NH4': 0.5},
        "uptake": {'NO3': 16.0, 'H2PO4': 1.2, 'SO4': 1.5, 'K': 9.5, 'Ca': 5.4, 'Mg': 2.4, 'NH4': 1.2}, 
        "default_ec": 3.0
    },
    "Concombre": {
        "targets": {'NO3': 18.0, 'H2PO4': 0.9, 'SO4': 3.5, 'K': 8.0, 'Ca': 6.5, 'Mg': 3.0, 'NH4': 0.5},
        "uptake": {'NO3': 16.0, 'H2PO4': 1.25, 'SO4': 1.3, 'K': 8.0, 'Ca': 4.0, 'Mg': 1.4, 'NH4': 1.0},
        "default_ec": 2.5
    },
    "Poivron (Sweet Pepper)": {
        "targets": {'NO3': 17.0, 'H2PO4': 1.2, 'SO4': 3.0, 'K': 5.0, 'Ca': 8.5, 'Mg': 3.0, 'NH4': 0.5},
        "uptake": {'NO3': 15.5, 'H2PO4': 1.25, 'SO4': 1.75, 'K': 6.5, 'Ca': 5.0, 'Mg': 1.5, 'NH4': 0.8},
        "default_ec": 2.5
    },
    "Fraise (Substrat)": {
        "targets": {'NO3': 11.0, 'H2PO4': 1.0, 'SO4': 2.5, 'K': 5.0, 'Ca': 4.5, 'Mg': 2.0, 'NH4': 0.5},
        "uptake": {'NO3': 9.0, 'H2PO4': 0.8, 'SO4': 1.0, 'K': 4.5, 'Ca': 2.5, 'Mg': 1.0, 'NH4': 0.5},
        "default_ec": 1.8
    },
    "Configuration Libre": {
        "targets": {'NO3': 12.0, 'H2PO4': 1.0, 'SO4': 1.5, 'K': 6.0, 'Ca': 3.0, 'Mg': 1.5, 'NH4': 0.5},
        "uptake": {'NO3': 12.0, 'H2PO4': 1.0, 'SO4': 1.0, 'K': 6.0, 'Ca': 3.0, 'Mg': 1.5, 'NH4': 1.0},
        "default_ec": 2.0
    }
}

# ==============================================================================
# PARTIE 3 : INTERFACE UTILISATEUR (STREAMLIT FRONTEND)
# ==============================================================================

# Configuration de la page
st.set_page_config(page_title="Voogt Fertigation Assistant", layout="wide", page_icon="🧪")

# En-tête Académique
st.title("🧪 Assistant de Recherche Fertigation")
st.markdown("""
**Méthodologie :** Basée sur l'algorithme de compensation universel de *Sonneveld & Voogt (2009)*.
**Objectif :** Calculer la composition de la solution nutritive (Goutte-à-goutte) en fonction de l'analyse du substrat.
""")
st.divider()

# Instanciation du calculateur
advisor = VoogtNutrientAlgorithm()
elements_order = ['NO3', 'H2PO4', 'SO4', 'K', 'Ca', 'Mg', 'NH4']

# --- BARRE LATÉRALE (SIDEBAR) ---
with st.sidebar:
    st.header("1. Configuration du Système")
    
    # Sélection Culture
    selected_crop = st.selectbox("📌 Profil de Culture", list(CROP_PROFILES.keys()))
    profile = CROP_PROFILES[selected_crop]
    
    st.divider()
    
    # Paramètres Algorithme
    st.subheader("Paramètres de Contrôle")
    target_ec = st.number_input("EC Cible Goutteur (dS/m)", value=profile["default_ec"], step=0.1, format="%.1f")
    correction_factor = st.slider("Facteur de Correction (%)", 0.0, 1.0, 0.5, 
                                  help="0.5 = Correction modérée (Recommandé). 1.0 = Correction totale immédiate.")
    
    st.info("""
    **Note Expert :** Un facteur de 0.5 signifie que l'on corrige 50% de l'écart détecté pour éviter les chocs osmotiques.
    """)

# --- ZONE PRINCIPALE DE SAISIE ---
st.subheader(f"2. Données d'Entrée : {selected_crop}")

# Initialisation des valeurs par défaut basées sur le profil
defaults_t = profile["targets"]
defaults_u = profile["uptake"]

with st.form("input_form"):
    # Création de 4 colonnes pour une saisie compacte
    c1, c2, c3, c4 = st.columns(4)
    
    inputs_target = {}
    inputs_analysis = {}
    inputs_water = {}
    inputs_uptake = {}

    with c1:
        st.markdown("##### 🎯 Cibles (Substrat)")
        st.caption("Normes (mmol/L)")
        for el in elements_order:
            val = defaults_t.get(el, 0.0)
            inputs_target[el] = st.number_input(f"{el} Cible", value=float(val), step=0.1, key=f"t_{el}", format="%.2f")

    with c2:
        st.markdown("##### 🧪 Analyse (Labo)")
        st.caption("Mesure réelle (mmol/L)")
        for el in elements_order:
            # Par défaut, on pré-remplit avec la cible (situation idéale) pour gagner du temps
            val = defaults_t.get(el, 0.0)
            inputs_analysis[el] = st.number_input(f"{el} Reçu", value=float(val), step=0.1, key=f"a_{el}", format="%.2f")

    with c3:
        st.markdown("##### 💧 Eau Source")
        st.caption("Contenu Eau Brute (mmol/L)")
        for el in elements_order:
            # Valeurs types eau de ville
            default_w = 0.5 if el in ['Ca', 'Mg', 'SO4'] else 0.0
            inputs_water[el] = st.number_input(f"{el} Eau", value=float(default_w), step=0.1, key=f"w_{el}", format="%.2f")

    with c4:
        st.markdown("##### 🌿 Absorption")
        st.caption("Conso. type (mmol/L)")
        for el in elements_order:
            val = defaults_u.get(el, 0.0)
            inputs_uptake[el] = st.number_input(f"{el} Abs", value=float(val), step=0.1, key=f"u_{el}", format="%.2f")

    submitted = st.form_submit_button("🚀 Lancer le Calcul Voogt", use_container_width=True)

# --- AFFICHAGE DES RÉSULTATS ---
if submitted:
    st.divider()
    st.header("3. Résultats et Prescriptions")
    
    # Appel de l'algorithme
    df_results, alerts, final_drip = advisor.calculate_drip_recipe(
        inputs_target, inputs_analysis, inputs_uptake, inputs_water, target_ec, correction_factor
    )

    # 1. Affichage des Alertes (Heuristiques)
    if alerts:
        with st.expander("⚠️ Rapports d'Anomalies & Sécurités", expanded=True):
            for a in alerts:
                if "🚨" in a: st.error(a)
                elif "📉" in a: st.warning(a)
                else: st.info(a)
    else:
        st.success("✅ Calcul nominal : Aucune contrainte majeure détectée.")

    # 2. Tableau principal
    st.subheader("📋 Tableau de Calcul Détaillé (mmol/L)")
    # Mise en forme du tableau pour mettre en évidence la colonne finale
    st.dataframe(
        df_results.style.background_gradient(subset=['Besoin Net (Engrais)'], cmap="Greens"),
        use_container_width=True
    )

    # 3. Visualisation Graphique
    st.subheader("📊 Visualisation des Écarts")
    col_graph1, col_graph2 = st.columns([2, 1])
    
    with col_graph1:
        # Comparaison Cible vs Analyse vs Solution Calculée
        chart_data = df_results[['Cible (Target)', 'Analyse (Labo)', 'Sol. Goutteur (Brut)']]
        st.bar_chart(chart_data)
        st.caption("Le but est que la 'Sol. Goutteur' compense la différence entre Cible et Analyse.")

    with col_graph2:
        # Focus sur l'apport net
        st.write("**Répartition des Apports (Net)**")
        st.bar_chart(df_results['Besoin Net (Engrais)'], color="#2ecc71")

    # 4. Exportation
    csv = df_results.to_csv().encode('utf-8')
    st.download_button(
        label="📥 Télécharger le Rapport (CSV)",
        data=csv,
        file_name=f'rapport_fertigation_{selected_crop.replace(" ", "_")}.csv',
        mime='text/csv',
    )
