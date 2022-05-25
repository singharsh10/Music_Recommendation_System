import re
import time
import spotipy
import pandas as pd
from collections import defaultdict
from spotipy.oauth2 import SpotifyClientCredentials
from multiprocessing.pool import ThreadPool as Pool
from sklearn.metrics.pairwise import cosine_similarity
from sklearn.feature_extraction.text import TfidfVectorizer


spotify_client_id = '5e21e93ba056426dbbdfc20981dc2ffb'
spotify_client_secret = '1fd1e8c5478043c89cd489c595879645'

spotify = spotipy.Spotify(
    auth_manager=SpotifyClientCredentials(client_id=spotify_client_id, client_secret=spotify_client_secret))


def fetch_track(song_name, song_artist):
    result = spotify.search(q='track: {} artist: {}'.format(song_name, song_artist), limit=1)

    # cannot find song return None
    if result['tracks']['items'] == []:
        return None

    song_id = result['tracks']['items'][0]['id']

    track_info = spotify.audio_features(tracks=song_id)
    track_info = track_info[0]

    track_data = defaultdict()
    for key, value in track_info.items():
        track_data[key] = value

    track_data['popularity'] = result['tracks']['items'][0]['popularity']
    track_data['duration_min'] = track_info['duration_ms'] / (1000 * 60)

    artist_id = result['tracks']['items'][0]['album']['artists'][0]['id']

    artist_info = spotify.artist(artist_id=artist_id)
    artist_genres = artist_info['genres']
    track_data['genres'] = [artist_genres]

    track_data['artists'] = artist_info['name']
    track_data['name'] = result['tracks']['items'][0]['name']
    track_data['release_year'] = result['tracks']['items'][0]['album']['release_date']
    track_data['release_year'] = int(track_data['release_year'][:4])

    return pd.DataFrame(track_data)


def normalize(spotify_df, column):
    max_val = spotify_df[column].max()
    min_val = spotify_df[column].min()

    for i in range(len(spotify_df[column])):
        spotify_df.at[i, column] = (spotify_df.at[i, column] - min_val) / (
                max_val - min_val)


# Normalising the value to bring them into range [0, 1]
# def normalisation(spotify_df):
#     spotify_df = normalize(spotify_df, 'tempo')
#
#     return spotify_df


def create_tf_idf(spotify_df):
    tfidf = TfidfVectorizer()
    tfidf_matrix = tfidf.fit_transform(spotify_df['genres'].apply(lambda x: " ".join(x)))
    genre_df = pd.DataFrame(tfidf_matrix.toarray())
    genre_df.columns = ['genre' + "|" + i for i in tfidf.get_feature_names_out()]
    genre_df.reset_index(drop=True, inplace=True)

    return genre_df


def one_hot_encoding(spotify_df):
    spotify_df['release_year'] = spotify_df['release_year'].apply(lambda x: int(x / 10))
    ohe = pd.get_dummies(spotify_df['release_year'])
    feature_names = ohe.columns
    ohe.columns = ['year' + "|" + str(i) for i in feature_names]
    ohe.reset_index(drop=True, inplace=True)

    return ohe


# creating the final useful set of features
def create_feature_set(spotify_df):
    genre_tf_idf = create_tf_idf(spotify_df)
    year_ohe = one_hot_encoding(spotify_df)

    feature_set_cols = ['tempo', 'valence', 'energy', 'danceability', 'acousticness', 'speechiness',
                        'popularity']

    spotify_df[feature_set_cols] *= 0.2
    year_ohe *= 0.1

    # putting inplace=True sets columns names to numeric numbers
    complete_feature_set = pd.concat([spotify_df[feature_set_cols], genre_tf_idf, year_ohe], axis=1)
    complete_feature_set['name'] = spotify_df['name']
    complete_feature_set['artists'] = spotify_df['artists']
    complete_feature_set['id'] = spotify_df['id']

    return complete_feature_set


def generate_recommendations(spotify_df, user_track_df):
    spotify_df['sim'] = cosine_similarity(spotify_df.drop(['name', 'artists', 'id'], axis=1),
                                          user_track_df.drop(['name', 'artists', 'id'], axis=1))

    spotify_df.sort_values(by='sim', ascending=False, inplace=True, kind="mergesort")
    spotify_df.reset_index(drop=True, inplace=True)

    return spotify_df.head(10)


########################################################################################################################
########################################################################################################################
########################################################################################################################


def begin(song_title, song_artist):
    tracks_df = pd.read_csv('tracks_with_genres_v3.csv')
    tracks_df.rename({'consolidates_genre_lists': 'genres'}, axis=1, inplace=True)
    tracks_df['genres'] = tracks_df['genres'].apply(lambda x: re.findall(r"'([^']*)'", str(x)))

    user_track = fetch_track(song_title, song_artist)

    if user_track is None:
        return None, None, None, None, None, None

    else:
        user_track['genres'] = user_track['genres'].apply(
            lambda x: [re.sub(' ', '_', i) for i in re.findall(r"'([^']*)'", str(x))])
        song_title = user_track.at[0, 'name']
        song_artist = user_track.at[0, 'artists']
        song_id = user_track.at[0, 'id']

        user_track.drop(['track_href', 'analysis_url', 'uri', 'type'], inplace=True, axis=1)
        tracks_df.drop(['explicit', 'release_date'], inplace=True, axis=1)
        tracks_df = pd.concat([user_track, tracks_df], ignore_index=True)

        tracks_df.drop_duplicates(subset=['artists', 'name'], keep='first', inplace=True, ignore_index=True)
        # although less probability but it can be possible that 2 tracks with same id as this dataset has different id's for some song
        tracks_df.drop_duplicates(subset=['id'], keep='first', inplace=True, ignore_index=True)

        tracks_df['popularity'] /= 100
        normalize(tracks_df, 'tempo')
        complete_feature_set = create_feature_set(tracks_df)

        """ use id to compare rather than artist and name """
        final_spotify_feature_set = complete_feature_set[complete_feature_set['id'] != song_id]
        final_user_track_feature = complete_feature_set[complete_feature_set['id'] == song_id]
        tracks_recommend = generate_recommendations(final_spotify_feature_set.copy(), final_user_track_feature)

        track_name = []
        track_artist = []
        track_url = []
        album_image = []

        def recommend(track_id):
            return spotify.track(track_id=track_id)

        track_ids = [tracks_recommend.at[i, 'id']
                     for i in range(len(tracks_recommend))]

        with Pool(5) as pool:
            for res in pool.map(recommend, track_ids):
                track_url.append(res['external_urls'])
                track_name.append(res['name'])
                track_artist.append(res['artists'][0]['name'])
                album_image.append(res['album']['images'][0]['url'])

        # for i in range(len(tracks_recommend)):
        #     track_id = tracks_recommend.at[i, 'id']
        #     # call to spotify api
        #     res = spotify.track(track_id=track_id)
        #     track_url.append(res['external_urls'])
        #     track_name.append(res['name'])
        #     album_image.append(res['album']['images'][0]['url'])
        #     # track_preview.append(res['album']['preview_url'])
        # # for i in range(len(track_name)):
        #     print(track_name[i], end=" ")
        #     print(track_url[i]['spotify'])

        return track_name, track_artist, track_url, album_image, song_title, song_artist

# start = time.time()
# begin("Heartless", "Weeknd")
# end = time.time()
# print("Time ", end-start)