# sort

> Recipes jiffy adds to the tldr page for `sort`.

- Show the most common lines in a file, with their counts:

`sort {{path/to/file}} | uniq -c | sort -rn | head -n {{n}}`
