# Reddit scheduler for systemd

A systemd service that allows you to schedule reddit posts for later posting.

The primary use case is taking advantage of sites like [Delay For Reddit](https://www.delayforreddit.com/analysis)
which tell you the optimal times to post on a subreddit, but without having to do it manually and without having
to pay for a subscription to their site.

While other reddit scheduling tools exist, they do not integrate with systemd, nor do they provide a nice CLI interface.

## Install

### AUR

Coming soon...

### Manual
```
git clone https://github.com/jeanlucthumm/reddit-systemd-scheduler
cd reddit-systemd-scheduler
sudo make install
```
If you want to uninstal:
```
sudo make uninstall
```


## Usage

There are two parts: a service that does the actual work and a client that talks to it. First
fill out the `RedditAPI` section in the config file at `~/.config/reddit-scheduler/config.ini`.
Check out [this Reddit thread](https://www.reddit.com/r/redditdev/comments/hasnnc/where_do_i_find_the_reddit_client_id_and_secret/) 
for how to get the client id and secret (you will have to create a new app on Reddit).

```
[RedditAPI]
Username = ...
Password = ...
ClientId = ...
ClientSecret = ...
```

Then, start the service:

```
systemctl --user start reddit-scheduler
```

Check that it started via the status logs. Any errors will be reported here:

```
systemctl --user status reddit-scheduler
```

Now you're free to use the CLI to schedule posts and such. Try running this first to see some of the options:
```
reddit --help
```
