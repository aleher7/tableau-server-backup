SELECT i.index_name, i.index_type, i.uniqueness, i.status,
       LISTAGG(c.column_name, ', ') WITHIN GROUP (ORDER BY c.column_position) AS columnas
FROM all_indexes i
JOIN all_ind_columns c
  ON c.index_owner = i.owner AND c.index_name = i.index_name
WHERE i.table_owner = 'CLABS_ANL_PRO' AND i.table_name = 'ANL_ACTIVIDAD_PT'
GROUP BY i.index_name, i.index_type, i.uniqueness, i.status
ORDER BY i.index_name;

SELECT grantee, privilege, grantable
FROM all_tab_privs
WHERE table_schema = 'CLABS_ANL_PRO' AND table_name = 'ANL_ACTIVIDAD_PT'
ORDER BY grantee, privilege;
