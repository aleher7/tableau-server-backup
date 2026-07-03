#!/usr/bin/env python3
"""
Script de Backup: Tableau → GitHub
Descarga workbooks de Tableau y los sube a GitHub automáticamente
"""

# ============================================================
# IMPORTAR LIBRERÍAS
# ============================================================

import os
import sys
import base64
import requests
import json
from datetime import datetime
from typing import List, Optional
import logging

# Configurar los mensajes que se muestran en pantalla
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ============================================================
# CLASE 1: CONECTAR A TABLEAU
# ============================================================

class TableauAPI:
    """Se conecta a Tableau Online/Server y descarga workbooks"""
    
    def __init__(self, server: str, site: str, username: str, password: str):
        # Guardar los datos de conexión
        self.server = server
        self.site = site
        self.username = username
        self.password = password
        self.token = None  # Se obtiene después de autenticarse
        self.base_url = f"{server}/api/3.17"  # URL base de la API de Tableau
        
        # Detectar si es Tableau Online o Server
        if "online.tableau.com" in server:
            self.tableau_type = "ONLINE"
            logger.info("Detectado: Tableau Online")
        else:
            self.tableau_type = "SERVER"
            logger.info("Detectado: Tableau Server")
    
    def authenticate(self) -> bool:
        """
        Conectarse a Tableau usando usuario y contraseña/token
        Devuelve True si funciona, False si falla
        """
        try:
            auth_url = f"{self.base_url}/auth/signin"
            
            # Crear el mensaje con las credenciales
            payload = {
                "credentials": {
                    "name": self.username,
                    "password": self.password,
                    "site": {
                        "contentUrl": self.site
                    }
                }
            }
            
            logger.info("Intentando autenticar...")
            logger.info(f"Servidor: {self.server}")
            logger.info(f"Sitio: {self.site}")
            logger.info(f"Usuario: {self.username}")
            
            # Convertir a JSON correctamente (para manejar caracteres especiales)
            json_payload = json.dumps(payload)
            
            # Enviar la solicitud de autenticación
            response = requests.post(
                auth_url,
                data=json_payload,
                headers={
                    "Content-Type": "application/json; charset=UTF-8"
                },
                timeout=10
            )
            
            logger.info(f"Respuesta del servidor: {response.status_code}")
            
            # Verificar si hubo error
            if response.status_code == 401:
                logger.error("Error 401: Credenciales invalidas")
                logger.error("El usuario, contraseña o token es incorrecto")
                return False
            
            if response.status_code == 403:
                logger.error("Error 403: Acceso prohibido")
                logger.error("Posible causa: Firewall o restricciones de la empresa")
                return False
            
            if response.status_code == 400:
                logger.error("Error 400: Bad Request")
                logger.error("Verifica el formato de los datos")
                return False
            
            # Si no hay error, procesar la respuesta
            response.raise_for_status()
            
            # Extraer el token de la respuesta
            data = response.json()
            self.token = data['credentials']['token']
            
            logger.info(f"Autenticacion exitosa en Tableau {self.tableau_type}")
            return True
            
        except requests.exceptions.RequestException as e:
            logger.error(f"Error de autenticacion: {e}")
            return False
        except Exception as e:
            logger.error(f"Error inesperado: {e}")
            return False
    
    def get_workbooks(self) -> List[dict]:
        """
        Obtener la lista de todos los workbooks disponibles
        Devuelve una lista de workbooks
        """
        try:
            # Usar el token para hacer la solicitud
            headers = {"X-Tableau-Auth": self.token}
            workbooks_url = f"{self.base_url}/sites/{self.site}/workbooks"
            
            logger.info("Obteniendo lista de workbooks...")
            
            # Solicitar la lista de workbooks
            response = requests.get(workbooks_url, headers=headers, timeout=10)
            response.raise_for_status()
            
            # Extraer la lista de workbooks
            data = response.json()
            workbook_list = data.get('workbook', [])
            
            logger.info(f"Se encontraron {len(workbook_list)} workbooks")
            return workbook_list
            
        except requests.exceptions.RequestException as e:
            logger.error(f"Error al obtener workbooks: {e}")
            return []
    
    def download_workbook(self, workbook_id: str, workbook_name: str, 
                          download_path: str) -> Optional[str]:
        """
        Descargar un workbook específico en formato .twbx
        Devuelve la ruta del archivo descargado
        """
        try:
            # Preparar la solicitud de descarga
            headers = {"X-Tableau-Auth": self.token}
            download_url = (f"{self.base_url}/sites/{self.site}/"
                           f"workbooks/{workbook_id}/content")
            
            logger.info(f"Descargando: {workbook_name}...")
            
            # Descargar el archivo
            response = requests.get(download_url, headers=headers, timeout=30)
            response.raise_for_status()
            
            # Guardar el archivo en el disco
            file_path = os.path.join(download_path, f"{workbook_name}.twbx")
            with open(file_path, 'wb') as f:
                f.write(response.content)
            
            logger.info(f"Descargado: {workbook_name}.twbx")
            return file_path
            
        except requests.exceptions.RequestException as e:
            logger.error(f"Error al descargar {workbook_name}: {e}")
            return None

# ============================================================
# CLASE 2: SUBIR A GITHUB
# ============================================================

class GitHubAPI:
    """Sube archivos al repositorio de GitHub"""
    
    def __init__(self, repo_owner: str, repo_name: str, token: str):
        # Guardar datos del repositorio
        self.repo_owner = repo_owner
        self.repo_name = repo_name
        self.token = token
        self.base_url = "https://api.github.com"
        # Preparar encabezados con autenticación
        self.headers = {
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github.v3+json"
        }
    
    def upload_file(self, file_path: str, github_path: str, 
                    commit_message: str) -> bool:
        """
        Subir un archivo a GitHub
        Si el archivo ya existe, lo actualiza
        Devuelve True si funciona, False si falla
        """
        try:
            # Leer el archivo
            with open(file_path, 'rb') as f:
                file_content = f.read()
            
            # Convertir a base64 (lo que requiere GitHub)
            encoded_content = base64.b64encode(file_content).decode('utf-8')
            
            # Construir la URL de GitHub para este archivo
            url = (f"{self.base_url}/repos/{self.repo_owner}/{self.repo_name}/"
                   f"contents/{github_path}")
            
            # Verificar si el archivo ya existe (para obtener su SHA)
            sha = None
            try:
                response = requests.get(url, headers=self.headers)
                if response.status_code == 200:
                    sha = response.json()['sha']
            except:
                pass
            
            # Preparar el contenido a enviar
            payload = {
                "message": commit_message,  # Mensaje del commit
                "content": encoded_content,  # Contenido en base64
                "branch": "main"  # Rama donde hacer push
            }
            
            # Si el archivo existe, incluir su SHA para actualizarlo
            if sha:
                payload["sha"] = sha
            
            # Enviar a GitHub
            response = requests.put(url, json=payload, headers=self.headers, timeout=10)
            response.raise_for_status()
            
            logger.info(f"Subido a GitHub: {github_path}")
            return True
            
        except requests.exceptions.RequestException as e:
            logger.error(f"Error al subir a GitHub: {e}")
            return False

# ============================================================
# FUNCIÓN PRINCIPAL
# ============================================================

def main():
    """
    Función principal que:
    1. Se conecta a Tableau
    2. Descarga todos los workbooks
    3. Los sube a GitHub
    """
    
    logger.info("=" * 70)
    logger.info("Backup Tableau → GitHub")
    logger.info("=" * 70)
    logger.info("")
    
    # ========== OBTENER VARIABLES DE ENTORNO ==========
    # Estas variables se configuran en GitHub Secrets
    tableau_server = os.getenv('TABLEAU_SERVER', '')
    tableau_site = os.getenv('TABLEAU_SITE', '')
    tableau_user = os.getenv('TABLEAU_USERNAME', '')
    tableau_password = os.getenv('TABLEAU_PASSWORD', '')
    
    github_owner = os.getenv('GITHUB_REPO_OWNER', '')
    github_repo = os.getenv('GITHUB_REPO_NAME', '')
    github_token = os.getenv('GITHUB_TOKEN', '')
    
    # ========== VERIFICAR QUE TODAS LAS VARIABLES EXISTAN ==========
    required_vars = [
        ('TABLEAU_SERVER', tableau_server),
        ('TABLEAU_SITE', tableau_site),
        ('TABLEAU_USERNAME', tableau_user),
        ('TABLEAU_PASSWORD', tableau_password),
        ('GITHUB_REPO_OWNER', github_owner),
        ('GITHUB_REPO_NAME', github_repo),
        ('GITHUB_TOKEN', github_token),
    ]
    
    for var_name, var_value in required_vars:
        if not var_value:
            logger.error(f"Variable de entorno requerida faltante: {var_name}")
            sys.exit(1)
    
    # ========== CREAR CARPETA DE DESCARGAS ==========
    download_dir = "tableau_workbooks"
    os.makedirs(download_dir, exist_ok=True)
    
    # ========== CONECTAR A TABLEAU ==========
    tableau = TableauAPI(tableau_server, tableau_site, tableau_user, tableau_password)
    if not tableau.authenticate():
        logger.error("No se pudo autenticar en Tableau")
        sys.exit(1)
    
    # ========== OBTENER LISTA DE WORKBOOKS ==========
    workbooks = tableau.get_workbooks()
    if not workbooks:
        logger.warning("No se encontraron workbooks")
        sys.exit(0)
    
    # ========== CONECTAR A GITHUB ==========
    github = GitHubAPI(github_owner, github_repo, github_token)
    
    logger.info("")
    logger.info("Descargando y subiendo workbooks...")
    logger.info("")
    
    # ========== DESCARGAR Y SUBIR CADA WORKBOOK ==========
    uploaded_count = 0
    for workbook in workbooks:
        # Obtener ID y nombre del workbook
        wb_id = workbook.get('id')
        wb_name = workbook.get('name')
        
        if not wb_id or not wb_name:
            continue
        
        # Descargar el workbook
        file_path = tableau.download_workbook(wb_id, wb_name, download_dir)
        if not file_path:
            continue
        
        # Preparar información para GitHub
        github_path = f"workbooks/{wb_name}.twbx"
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        commit_message = f"Backup: {wb_name} - {timestamp}"
        
        # Subir a GitHub
        if github.upload_file(file_path, github_path, commit_message):
            uploaded_count += 1
    
    # ========== RESUMEN FINAL ==========
    logger.info("")
    logger.info("=" * 70)
    logger.info(f"Completado: {uploaded_count}/{len(workbooks)} workbooks")
    logger.info("=" * 70)

# ========== EJECUTAR ==========
if __name__ == "__main__":
    main()
