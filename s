LEFT JOIN (SELECT '005Qu00000BgaYbIAJ' AS ID_AGENTE_SF,
                  DATE '2026-09-01'    AS FECHA_DESDE,
                  CAST(NULL AS DATE)   AS FECHA_HASTA
           FROM dual) JR
       ON JR.ID_AGENTE_SF = A.ID_Agente
      AND A.Fecha_Visita >= JR.FECHA_DESDE
      AND (JR.FECHA_HASTA IS NULL OR A.Fecha_Visita <= JR.FECHA_HASTA)
