import streamlit as st
import pandas as pd
import numpy as np

# ==========================================
# PARTIE 1 : LE MOTEUR DE CALCUL (BACKEND)
# ==========================================

class VoogtNutrientAlgorithm:
    """
    Implémentation de l'Algorithme Universel de Calcul de Solutions Nutritives (Sonneveld & Voogt).
    """

    def __init__(self):
        # Valences pour équilibre électrique
        self.valences = {
            'K': 1, 'Ca': 2, 'Mg': 2, 'NH4': 1, 'Na': 1,
            'NO3': 1, 'SO4': 2, 'H2PO4': 1, 'Cl': 1, 'HCO3': 1
        }
        # Poids moléculaires pour conversion future (mg/mmol)
        self.molar_weights = {
            'K': 39.1, 'Ca': 40.1, 'Mg': 24.3, 'NH4': 18.0, 
            'NO3': 62.0, 'SO4': 96.1, 'H2PO4': 97.0
        }

    def calculate_drip_recipe(self, target_vals, analysis_vals, uptake_vals, water_vals, target_ec, correction_factor):
        elements = ['K', 'Ca', 'Mg', 'NH4', 'NO3', 'SO4', 'H2PO4']
        warnings = []
        
        # 1. Calcul de l'ajustement (Feedback)
        adjusted_vals = {}
        for el in elements:
            gap = target_vals.get(el, 0) - analysis_vals.get(el, 0)
            
            # Heuristique : Plafonnement de la correction
            max_correction = target_vals.get(el, 0) * 0.6 # Max 60% de correction
            correction_term = gap * correction_factor
            
            if abs(correction_term) > max_correction:
                correction_term = max_correction if gap > 0 else -max_correction
                warnings.append(f"⚠️ {el} : Correction plafonnée (Écart trop grand)")

            base_calc = uptake_vals.get(el, 0) + correction_term
            if base_calc < 0:
                base_calc = 0
                warnings.append(f"📉 {el} : Stock substrat critique. Apport coupé temporairement.")
            
            adjusted_vals[el] = base_calc

        # 2. Équilibrage Ionique
        sum_cations = sum(adjusted_vals[el] * self.valences[el] for el in ['K', 'Ca', 'Mg', 'NH4'] if el in adjusted_vals)
        sum_anions = sum(adjusted_vals[el] * self.valences[el] for el in ['NO3', 'SO4', 'H2PO4'] if el in adjusted_vals)
        
        imbalance = sum_cations - sum_anions
        
        if imbalance > 0.1: 
            adjusted_vals['NO3'] += imbalance / self.valences['NO3'] # Ajout Anion
        elif imbalance < -0.1:
            missing = abs(imbalance)
            adjusted_vals['K'] += (missing * 0.5) / self.valences['K'] # Ajout Cation (Mix K/Ca)
            adjusted_vals['Ca'] += (missing * 0.5) / self.valences['Ca']

        # 3. Correction EC
        current_meq = sum(adjusted_vals[el] * self.valences[el] for el in ['K', 'Ca', 'Mg', 'NH4'])
        estimated_ec = current_meq / 10.0 # Approximation empirique
        if estimated_ec < 0.1: estimated_ec = 0.1
        
        ec_ratio = target_ec / estimated_ec
        
        final_drip_conc = {}
        for el in elements:
            if el in ['NH4', 'H2PO4']: # Éléments souvent fixes
                final_drip_conc[el] = adjusted_vals[el] 
            else:
                final_drip_conc[el] = adjusted_vals[el] * ec_ratio

        # 4. Soustraction Eau Brute
        fertilizer_needs = {}
        for el in elements:
            water_content = water_vals.get(el, 0)
            need = final_drip_conc[el] - water_content
            if need < 0:
                need = 0
                warnings.append(f"🚨 {el} : Eau de source trop riche (Surcharge).")
            fertilizer_needs[el] = need

        # Création DataFrame
        df_results = pd.DataFrame({
            'Cible (Target)': target_vals,
            'Analyse (Labo)': analysis_vals,
            'Besoin Net (Engrais)': fertilizer_needs
        })
        
        return df_results.round(2), warnings, final_drip_conc

# ==========================================
# PARTIE 2 : L'INTERFACE UTILISATEUR (STREAMLIT)
# ==========================================

st.set_page_config(page_title="Voogt Research Assistant", layout="wide")

st.title("🌱 Assistant de Fertigation - Méthode Sonneveld & Voogt")
st.markdown("""
**Outil de Recherche Académique et Pratique.** Cet outil calcule la composition idéale de la solution goutte-à-goutte en compensant les écarts entre les valeurs cibles (théorie) et les analyses de substrat (réalité).
""")

# --- SIDEBAR : Paramètres Globaux ---
st.sidebar.header("1. Paramètres de Contrôle")
target_ec = st.sidebar.number_input("EC Cible du Goutteur (dS/m)", value=2.5, step=0.1)
correction_factor = st.sidebar.slider("Facteur de Correction (%)", 0.0, 1.0, 0.5, 
                                      help="0.0 = Pas de correction (Standard). 1.0 = Correction totale immédiate (Risqué).")

st.sidebar.info("Le facteur 0.5 est recommandé par Sonneveld pour éviter les oscillations osmotiques.")

# --- MAIN : Formulaire de Données ---
advisor = VoogtNutrientAlgorithm()
elements = ['NO3', 'H2PO4', 'SO4', 'K', 'Ca', 'Mg', 'NH4']

# Valeurs par défaut (Exemple Concombre Chapitre 12)
default_targets = [12.0, 1.25, 1.5, 6.5, 2.75, 1.5, 1.0]
default_analysis = [10.0, 1.0, 1.8, 6.0, 3.5, 1.2, 0.5] # Ca élevé, K bas
default_water = [0.0, 0.0, 0.5, 0.0, 0.6, 0.3, 0.0]
default_uptake = [13.0, 1.0, 1.0, 7.0, 2.0, 1.0, 1.2]

st.subheader("2. Saisie des Données Chimiques (mmol/L)")

with st.form("data_input"):
    col1, col2, col3, col4 = st.columns(4)
    
    with col1:
        st.markdown("### 🎯 Cibles (Substrat)")
        st.caption("Normes bibliographiques")
        inputs_target = {}
        for i, el in enumerate(elements):
            inputs_target[el] = st.number_input(f"{el} Cible", value=default_targets[i], key=f"t_{el}")

    with col2:
        st.markdown("### 🧪 Analyse (Labo)")
        st.caption("Drainage ou Extrait 1:2")
        inputs_analysis = {}
        for i, el in enumerate(elements):
            inputs_analysis[el] = st.number_input(f"{el} Reçu", value=default_analysis[i], key=f"a_{el}")

    with col3:
        st.markdown("### 💧 Eau Source")
        st.caption("Contenu de l'eau brute")
        inputs_water = {}
        for i, el in enumerate(elements):
            inputs_water[el] = st.number_input(f"{el} Eau", value=default_water[i], key=f"w_{el}")

    with col4:
        st.markdown("### 🌿 Absorption Ref.")
        st.caption("Consommation théorique")
        inputs_uptake = {}
        for i, el in enumerate(elements):
            inputs_uptake[el] = st.number_input(f"{el} Abs", value=default_uptake[i], key=f"u_{el}")

    submitted = st.form_submit_button("🚀 Lancer l'Algorithme de Voogt")

# --- RÉSULTATS ---
if submitted:
    st.divider()
    st.subheader("3. Résultats et Prescriptions")
    
    # Appel de l'algorithme
    df, alerts, final_drip = advisor.calculate_drip_recipe(
        inputs_target, inputs_analysis, inputs_uptake, inputs_water, target_ec, correction_factor
    )

    # Affichage des alertes heuristiques
    if alerts:
        with st.expander("⚠️ Rapports d'Heuristiques & Sécurité", expanded=True):
            for alert in alerts:
                if "🚨" in alert:
                    st.error(alert)
                else:
                    st.warning(alert)
    else:
        st.success("✅ Aucune anomalie majeure détectée. Calcul nominal.")

    # Affichage Tabulaire et Graphique
    res_col1, res_col2 = st.columns([1, 1])

    with res_col1:
        st.markdown("#### Tableau de Prescription (mmol/L)")
        st.dataframe(df.style.highlight_max(axis=0, color='#f0f2f6'), use_container_width=True)
        
        st.markdown("##### 📝 Interprétation Rapide")
        st.write("La colonne **'Besoin Net'** indique ce que vous devez réellement injecter via vos bacs d'engrais (A et B) après avoir pris en compte ce que la plante boit, ce que le sol contient déjà, et ce que l'eau apporte.")

    with res_col2:
        st.markdown("#### Visualisation des Écarts")
        # Préparation des données pour le graph
        chart_data = df[['Cible (Target)', 'Analyse (Labo)']].copy()
        chart_data['Solution Calculée'] = pd.Series(final_drip)
        
        st.bar_chart(chart_data)
        st.caption("Comparez les barres : Si l'Analyse (Orange) est plus basse que la Cible (Bleue), la Solution Calculée (Rouge/Verte) devrait être plus haute pour compenser.")

    # Exportation pour la recherche
    csv = df.to_csv().encode('utf-8')
    st.download_button(
        label="📥 Télécharger les résultats (CSV) pour Rapport de Recherche",
        data=csv,
        file_name='resultats_fertigation_voogt.csv',
        mime='text/csv',
    )
