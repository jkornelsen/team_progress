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
```
