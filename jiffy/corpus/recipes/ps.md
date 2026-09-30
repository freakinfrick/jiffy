# ps

> Recipes jiffy adds to the tldr page for `ps`.

- Sort processes by memory use, largest first, and keep the top `n`:

`ps aux --sort=-%mem | head -n {{n}}`

- Sort processes by CPU use, largest first, and keep the top `n`:

`ps aux --sort=-%cpu | head -n {{n}}`
