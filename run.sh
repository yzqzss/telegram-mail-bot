docker run --name telegram-mail-bot \
  -d -it --restart always \
  -v $PWD/.env:/workdir/.env -v $PWD/conf:/workdir/conf -v $PWD:/workdir/src \
  nyamisty/telegram-mail-bot
