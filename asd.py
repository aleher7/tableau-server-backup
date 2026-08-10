--------------------------------------------------------------------------------
-- MDM_TABLEAU_GIT_CONTENT
--------------------------------------------------------------------------------
-- Registra cada VERSION de cada workbook que ha pasado (o va a pasar) por el
-- backup de GitHub. Es la tabla de control del versionado -- reemplaza al
-- "sobrescribir siempre el mismo archivo" que hacia el sistema hasta ahora.
--
-- Una fila = una version concreta de un workbook concreto.
-- Varias filas pueden compartir WORKBOOK_LUID (una por cada version que se
-- ha conservado en algun momento).
--------------------------------------------------------------------------------

CREATE TABLE MDM_TABLEAU_GIT_CONTENT
(
    WORKBOOK_LUID     VARCHAR2(100 BYTE)  NOT NULL,
    FILE_TYPE         VARCHAR2(500 BYTE),
    NAME              VARCHAR2(500 BYTE),
    VERSION           NUMBER,
    NAVIGATION        VARCHAR2(500 BYTE),
    DATE_UPLOAD       TIMESTAMP,
    FLG_LAST_VERSION  NUMBER(1),
    FLG_DELETE        NUMBER(1) DEFAULT 0,
    DATE_DELETE       TIMESTAMP
)
;
