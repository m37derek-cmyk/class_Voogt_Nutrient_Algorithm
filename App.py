import streamlit as st
import pandas as pd
import numpy as np
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from datetime import datetime

# ==============================================================================
# CONFIGURATION ET SÉCURITÉ (A MODIFIER PAR L'UTILISATEUR)
# ==============================================================================
# Mettez ici le nom exact de votre fichier clé téléchargé depuis Google Cloud
GOOGLE_JSON_FILE = "votre-fichier-cle-secrete.json" 

# Mettez ici le nom exact de votre fichier Google Sheet (créé sur votre Drive)
SHEET_NAME = "Donnees_Fertigation_Raw"

# ==============================================================================
# MODULE 1 : GESTIONNAIRE DE BASE DE DONNÉES (LOGGER)
# ==============================================================================
class DataLogger:
    """
    Gère la connexion sécurisée vers Google Sheets pour l'archivage longitudinal.
    """
    def __init__(self, json_key_file, sheet_name):
        self.json_file = json_key_file
        self.sheet_name = sheet_name
        # Définition des droits d'accès (Scopes)
        self.scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]

    def log_experiment(self, crop_name, targets, analysis, final_drip, ec_target):
        """
        Connecte au Cloud et ajoute une ligne d'historique.
        """
        try:
            # Authentification
            creds = ServiceAccountCredentials.from_json_keyfile_name(self.json_file, self.scope)
            client = gspread.authorize(creds)
            
            # Ouverture du classeur
            sheet = client.open(self.sheet_name).sheet1
            
            # Création de l'horodatage
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            
            # Construction de la ligne de données (Flat Data)
            # Structure : [Date, Culture, EC_Cible, NO3_T, NO3_A, NO3_Res, ..., NH4_T, NH4_A, NH4_Res]
            row = [timestamp, crop_name, ec_target]
            
            elements = ['NO3', 'H2PO4', 'SO4', 'K', 'Ca', 'Mg', 'NH4']
            
            for el in elements:
                row.append(float(targets.get(el, 0)))    # T = Target (Cible)
                row.append(float(analysis.get(el, 0)))   # A = Analysis (Reçu)
                row.append(float(final_drip.get(el, 0))) # Res = Resultat (Goutteur)
                
            # Envoi vers le Cloud
            sheet.append_row(row)
            return True, "✅ Données archivées avec succès dans Google Sheets."
            
        except FileNotFoundError:
            return False, f"❌ Fichier clé '{self.json_file}' introuvable. Vérifiez le dossier."
        except Exception as e:
            return False, f"❌ Erreur API Google : {str(e)}"

# ==============================================================================
# MODULE 2 : MOTEUR DE CALCUL SCIENTIFIQUE (VOOGT)
# ==============================================================================
class VoogtNutrientAlgorithm:
    def __init__(self):
        self.valences = {
            'K': 1, 'Ca': 2, 'Mg': 2, 'NH4': 1, 'Na': 1,
            'NO3': 1, 'SO4': 2, 'H2PO4': 1, 'Cl': 1, 'HCO3': 1
        }

    def calculate_drip_recipe(self, target_vals, analysis_vals, uptake_vals, water_vals, target_ec, correction_factor):
        elements = ['NO3', 'H2PO4', 'SO4', 'K', 'Ca', 'Mg', 'NH4']
        warnings = []
        
        # A. Feedback (Ajustement)
        adjusted_vals = {}
        for el in elements:
            gap = target_vals.get(el, 0.0) - analysis_vals.get(el, 0.0)
            
            # Heuristique : Plafonnement
            max_correction = target_vals.get(el, 0.0) * 0.6
            correction_term = gap * correction_factor
            
            if abs(correction_term) > max_correction:
                correction_term = max_correction if gap > 0 else -max_correction
                warnings.append(f"⚠️ {el} : Correction plafonnée (Sécurité).")

            base_calc = uptake_vals.get(el, 0.0) + correction_term
            if base_calc < 0: base_calc = 0
            
            adjusted_vals[el] = base_calc

        # B. Équilibrage Ionique
        sum_cations = sum(adjusted_vals[el] * self.valences[el] for el in ['K', 'Ca', 'Mg', 'NH4'])
        sum_anions = sum(adjusted_vals[el] * self.valences[el] for el in ['NO3', 'SO4', 'H2PO4'])
        imbalance = sum_cations - sum_anions
        
        if imbalance > 0.1: 
            adjusted_vals['NO3'] += imbalance / self.valences['NO3']
        elif imbalance < -0.1:
            missing = abs(imbalance)
            adjusted_vals['K'] += (missing * 0.5) / self.valences['K']
            adjusted_vals['Ca'] += (missing * 0.5) / self.valences['Ca']

        # C. Correction EC
        current_meq = sum(adjusted_vals[el] * self.valences[el] for el in ['K', 'Ca', 'Mg', 'NH4'])
        if current_meq < 0.1: current_meq = 0.1
        estimated_ec = current_meq / 10.0 
        if estimated_ec < 0.2: estimated_ec = 0.2
        
        ec_ratio = target_ec / estimated_ec
        
        final_drip_conc = {}
        for el in elements:
            if el in ['NH4', 'H2PO4']: 
                final_drip_conc[el] = adjusted_vals[el] 
            else:
                final_drip_conc[el] = adjusted_vals[el] * ec_ratio

        # D. Soustraction Eau Brute
        fertilizer_needs = {}
        for el in elements:
            need = final_drip_conc[el] - water_vals.get(el, 0.0)
            if need < 0:
                need = 0
                warnings.append(f"🚨 {el} : Surcharge via Eau de Source.")
            fertilizer_needs[el] = need

        df_results = pd.DataFrame({
            'Cible': target_vals,
            'Analyse': analysis_vals,
            'Goutteur': final_drip_conc,
            'Besoin Net': fertilizer_needs
        })
        
        return df_results.round(2), warnings, final_drip_conc

# ==============================================================================
# MODULE 3 : INTERFACE UTILISATEUR (STREAMLIT)
# ==============================================================================

# Données de référence (Sonneveld Annex C)
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
    "Poivron": {
        "targets": {'NO3': 17.0, 'H2PO4': 1.2, 'SO4': 3.0, 'K': 5.0, 'Ca': 8.5, 'Mg': 3.0, 'NH4': 0.5},
        "uptake": {'NO3': 15.5, 'H2PO4': 1.25, 'SO4': 1.75, 'K': 6.5, 'Ca': 5.0, 'Mg': 1.5, 'NH4': 0.8},
        "default_ec": 2.5
    }
}

st.set_page_config(page_title="Voogt Research Assistant", layout="wide", page_icon="📡")

st.title("📡 Système Intégré de Fertigation (Voogt & Cloud Data)")
st.markdown("**Version Connectée.** Les calculs sont effectués localement, les données sont archivées sur le serveur académique (Google Sheets).")

# --- SIDEBAR ---
with st.sidebar:
    st.header("Paramètres")
    selected_crop = st.selectbox("Culture", list(CROP_PROFILES.keys()))
    profile = CROP_PROFILES[selected_crop]
    target_ec = st.number_input("EC Cible", value=profile["default_ec"], step=0.1)
    correction_factor = st.slider("Facteur Correction", 0.0, 1.0, 0.5)

# --- SAISIE ---
defaults_t = profile["targets"]
defaults_u = profile["uptake"]
elements_order = ['NO3', 'H2PO4', 'SO4', 'K', 'Ca', 'Mg', 'NH4']

with st.form("input_form"):
    c1, c2, c3, c4 = st.columns(4)
    inputs_target = {}
    inputs_analysis = {}
    inputs_water = {}
    inputs_uptake = {}

    with c1:
        st.write("Target (Cible)")
        for el in elements_order:
            inputs_target[el] = st.number_input(f"{el} T", value=defaults_t.get(el, 0.0), key=f"t_{el}")
    with c2:
        st.write("Analyse (Reçu)")
        for el in elements_order:
            inputs_analysis[el] = st.number_input(f"{el} A", value=defaults_t.get(el, 0.0), key=f"a_{el}")
    with c3:
        st.write("Eau Source")
        for el in elements_order:
            val = 0.5 if el in ['Ca', 'Mg', 'SO4'] else 0.0
            inputs_water[el] = st.number_input(f"{el} E", value=val, key=f"w_{el}")
    with c4:
        st.write("Absorption")
        for el in elements_order:
            inputs_uptake[el] = st.number_input(f"{el} Abs", value=defaults_u.get(el, 0.0), key=f"u_{el}")

    submitted = st.form_submit_button("🚀 Calculer")

# --- TRAITEMENT ---
if submitted:
    advisor = VoogtNutrientAlgorithm()
    df_results, alerts, final_drip = advisor.calculate_drip_recipe(
        inputs_target, inputs_analysis, inputs_uptake, inputs_water, target_ec, correction_factor
    )

    st.divider()
    
    # Section Résultats
    r1, r2 = st.columns([2, 1])
    with r1:
        st.subheader("Résultats Numériques")
        st.dataframe(df_results.style.background_gradient(subset=['Besoin Net'], cmap="Greens"), use_container_width=True)
    with r2:
        st.subheader("Contrôle Qualité")
        if alerts:
            for a in alerts: st.warning(a)
        else:
            st.success("Paramètres nominaux.")

    st.divider()
    
    # Section Archivage (Cloud)
    st.subheader("💾 Archivage des Données (Cloud)")
    col_cloud1, col_cloud2 = st.columns([3, 1])
    
    with col_cloud1:
        st.info(f"Destination : Google Sheet '{SHEET_NAME}'. Assurez-vous que le fichier JSON est présent.")
        
    with col_cloud2:
        if st.button("Envoyer vers Google Sheets"):
            logger = DataLogger(GOOGLE_JSON_FILE, SHEET_NAME)
            success, msg = logger.log_experiment(selected_crop, inputs_target, inputs_analysis, final_drip, target_ec)
            
            if success:
                st.balloons()
                st.success(msg)
            else:
                st.error(msg)
