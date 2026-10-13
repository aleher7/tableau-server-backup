SELECT USER FROM dual;

SELECT owner, object_name, object_type
FROM all_objects
WHERE object_name = 'ANL_ACTIVIDAD_PT';


SELECT index_name, uniqueness, status FROM dba_indexes
WHERE table_owner = 'CLABS_ANL_PRO' AND table_name = 'ANL_ACTIVIDAD_PT';

SELECT grantee, privilege, grantable FROM dba_tab_privs
WHERE owner = 'CLABS_ANL_PRO' AND table_name = 'ANL_ACTIVIDAD_PT';
