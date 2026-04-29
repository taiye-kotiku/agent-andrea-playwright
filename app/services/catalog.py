"""
Catalog management for operators and services.
"""

import json
import logging
from pathlib import Path
from typing import Optional, Any, Dict

logger = logging.getLogger(__name__)

OPERATOR_CATALOG_FILE = Path("operator_catalog.json")
SERVICE_CATALOG_FILE = Path("service_catalog.json")

operator_catalog = {
    "updated_at": None,
    "operators": {}
}

service_catalog = {
    "updated_at": None,
    "services": {}
}


def load_operator_catalog():
    """Load operator catalog from disk."""
    global operator_catalog
    try:
        if OPERATOR_CATALOG_FILE.exists():
            operator_catalog = json.loads(OPERATOR_CATALOG_FILE.read_text(encoding="utf-8"))
            logger.info("👥 Operator catalog loaded from disk")
    except Exception as e:
        logger.warning(f"Failed to load operator catalog: {e}")


def save_operator_catalog():
    """Save operator catalog to disk."""
    try:
        OPERATOR_CATALOG_FILE.write_text(
            json.dumps(operator_catalog, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )
        logger.info("💾 Operator catalog saved")
    except Exception as e:
        logger.warning(f"Failed to save operator catalog: {e}")


def load_service_catalog():
    """Load service catalog from disk."""
    global service_catalog
    try:
        if SERVICE_CATALOG_FILE.exists():
            service_catalog = json.loads(SERVICE_CATALOG_FILE.read_text(encoding="utf-8"))
            logger.info("💈 Service catalog loaded from disk")
    except Exception as e:
        logger.warning(f"Failed to load service catalog: {e}")


def save_service_catalog():
    """Save service catalog to disk."""
    try:
        SERVICE_CATALOG_FILE.write_text(
            json.dumps(service_catalog, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )
        logger.info("💾 Service catalog saved")
    except Exception as e:
        logger.warning(f"Failed to save service catalog: {e}")


async def update_operator_catalog_from_page(page) -> dict:
    """Extract operator information from Wegest page."""
    try:
        found = await page.evaluate("""
            () => {
                const result = {};
                document.querySelectorAll('.operatori_nomi .operatore[id_operatore]').forEach(op => {
                    const id = op.getAttribute('id_operatore');
                    if (!id || id === '0') return;

                    const nome = op.querySelector('.nome');
                    if (!nome) return;

                    result[id] = {
                        name: nome.textContent.trim(),
                        active: !op.classList.contains('assente')
                    };
                });
                return result;
            }
        """)

        if found and isinstance(found, dict):
            for op_id, info in found.items():
                operator_catalog["operators"][op_id] = info

            operator_catalog["updated_at"] = datetime.utcnow().isoformat()
            save_operator_catalog()
            logger.info(f"👥 Operator catalog updated: {list(found.values())}")

        return found or {}
    except Exception as e:
        logger.warning(f"Failed to update operator catalog from page: {e}")
        return {}


async def update_service_catalog_from_page(page) -> dict:
    """Extract service information from Wegest page."""
    try:
        found = await page.evaluate("""
            () => {
                const result = {};
                document.querySelectorAll('.pulsanti_tab .servizio[nome]').forEach(s => {
                    const nome = (s.getAttribute('nome') || '').trim();
                    if (!nome) return;

                    const key = nome.toLowerCase();
                    result[key] = {
                        id: s.id || '',
                        nome: nome,
                        tempo_operatore: parseInt(s.getAttribute('tempo_operatore') || '0', 10),
                        tempo_cliente: parseInt(s.getAttribute('tempo_cliente') || '0', 10)
                    };
                });
                return result;
            }
        """)

        if found and isinstance(found, dict):
            for key, info in found.items():
                service_catalog["services"][key] = info

            service_catalog["updated_at"] = datetime.utcnow().isoformat()
            save_service_catalog()
            logger.info(f"💈 Service catalog updated: {list(found.keys())[:10]}")

        return found or {}
    except Exception as e:
        logger.warning(f"Failed to update service catalog from page: {e}")
        return {}


async def extract_service_operator_durations_from_page(page) -> dict:
    """Extract service durations from Wegest page."""
    durations = await page.evaluate("""
        () => {
            const map = {};
            document.querySelectorAll('.pulsanti_tab .servizio').forEach(s => {
                const nome = (s.getAttribute('nome') || '').toLowerCase().trim();
                const tempoOperatore = parseInt(s.getAttribute('tempo_operatore') || '0', 10);
                if (nome) {
                    map[nome] = tempoOperatore;
                }
            });
            return map;
        }
    """)
    return durations or {}
