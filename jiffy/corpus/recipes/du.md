# du

> Recipes jiffy adds to the tldr page for `du`.

- Show the biggest folders and files in a directory, largest first:

`du -sh {{path/to/directory}}/* | sort -rh | head -n {{n}}`
