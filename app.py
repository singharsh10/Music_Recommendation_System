from flask import Flask, render_template, request
import main

app = Flask(__name__)


@app.route("/", methods=["POST", "GET"])
def index():
    if request.method == "POST":
        form = request.form
        song_title = str(form['track_name'])
        song_artist = str(form['track_artist'])
        song_name, song_artist, song_url, album_image, user_song_name, user_song_artist = main.begin(
            song_title=song_title,
            song_artist=song_artist)
        if song_name is None and song_url is None:
            return render_template("index.html",
                                   error="Could not find that track. Make sure you entered the details correctly")

        return render_template("results.html", song_name=song_name, song_artist=song_artist,
                               song_url=song_url, album_image=album_image, user_song=user_song_name,
                               user_artist=user_song_artist)

    if request.method == "GET":
        return render_template('index.html')


if __name__ == '__main__':
    app.run(debug=True)