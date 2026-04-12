# Other useful commands not described in Installation.md

## powershell

```
(gci *.py) + (gci app/*.py -recurse) + (gci app/templates/* -recurse) | sls -patt "recipe_attrib_reqs" -ca

pip show flask
```

## psql queries

```
\d
\pset pager off
SET client_encoding TO 'UTF8';

select * from items where item_id in (18,19);
select * from recipes where item_id in (18,19);
select * from recipe_sources where id=17;
select * from progress where id in (18,4);
select * from loc_items where item_id in (18,19);
```
