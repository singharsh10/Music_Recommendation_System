import re
import os
import spotipy
import pandas as pd
from dotenv import load_dotenv
from collections import defaultdict
from spotipy.oauth2 import SpotifyClientCredentials
from multiprocessing.pool import ThreadPool as Pool
from sklearn.metrics.pairwise import cosine_similarity
from sklearn.feature_extraction.text import TfidfVectorizer

load_dotenv()

spotify_client_id = os.getenv('CLIENT_ID')
spotify_client_secret = os.getenv('CLIENT_SECRET')

spotify = spotipy.Spotify(
    auth_manager=SpotifyClientCredentials(client_id=spotify_client_id, client_secret=spotify_client_secret))


# fetching the information regarding the track that user entered
def fetch_track(song_name, song_artist):
    result = spotify.search(q='track: {} artist: {}'.format(song_name, song_artist), limit=1)

    # not able to find song (return None)
    if result['tracks']['items'] == []:
        return None

    song_id = result['tracks']['items'][0]['id']

    # retrieving the user tracks features from spotify api
    track_info = spotify.audio_features(tracks=song_id)
    track_info = track_info[0]

    track_data = defaultdict()
    for key, value in track_info.items():
        track_data[key] = value

    artist_id = result['tracks']['items'][0]['album']['artists'][0]['id']

    artist_info = spotify.artist(artist_id=artist_id)
    artist_genres = artist_info['genres']
    track_data['genres'] = [artist_genres]
    track_data['artists'] = artist_info['name']
    track_data['name'] = result['tracks']['items'][0]['name']
    track_data['release_year'] = result['tracks']['items'][0]['album']['release_date']
    track_data['popularity'] = result['tracks']['items'][0]['popularity']
    track_data['duration_min'] = track_info['duration_ms'] / (1000 * 60)
    track_data['release_year'] = int(track_data['release_year'][:4])

    return pd.DataFrame(track_data)


# normalizing specific columns of spotify dataset to bring them down to range [0,1]
def normalize(spotify_df, column):
    max_val = spotify_df[column].max()
    min_val = spotify_df[column].min()

    for i in range(len(spotify_df[column])):
        spotify_df.at[i, column] = (spotify_df.at[i, column] - min_val) / (
                max_val - min_val)


# create TF-IDF features for genre column of the spotify dataset
def create_tf_idf(spotify_df):
    tfidf = TfidfVectorizer()
    tfidf_matrix = tfidf.fit_transform(spotify_df['genres'].apply(lambda x: " ".join(x)))
    genre_df = pd.DataFrame(tfidf_matrix.toarray())
    genre_df.columns = ['genre' + "|" + i for i in tfidf.get_feature_names_out()]
    genre_df.reset_index(drop=True, inplace=True)

    return genre_df


# one hot encoding the release year column of the spotify dataset
def one_hot_encoding(spotify_df):
    spotify_df['release_year'] = spotify_df['release_year'].apply(lambda x: int(x / 10))
    ohe = pd.get_dummies(spotify_df['release_year'])
    feature_names = ohe.columns
    ohe.columns = ['year' + "|" + str(i) for i in feature_names]
    ohe.reset_index(drop=True, inplace=True)

    return ohe


# creating the final useful set of features which will be used to compute similarity
def create_feature_set(spotify_df):
    genre_tf_idf = create_tf_idf(spotify_df)
    year_ohe = one_hot_encoding(spotify_df)

    feature_set_cols = ['tempo', 'valence', 'energy', 'danceability', 'acousticness', 'speechiness',
                        'popularity', 'instrumentalness']

    spotify_df[feature_set_cols] *= 0.2
    year_ohe *= 0.1

    # concatenating final set of features into complete_feature_set
    complete_feature_set = pd.concat([spotify_df[feature_set_cols], genre_tf_idf, year_ohe], axis=1)
    # track id will be used later on to find information regarding tracks that have to be recommended to the user
    complete_feature_set['id'] = spotify_df['id']

    return complete_feature_set


# finding similarity score between user song and spotify dataset songs
def generate_recommendations(spotify_df, user_track_df):
    spotify_df['sim'] = cosine_similarity(spotify_df.drop(['id'], axis=1),
                                          user_track_df.drop(['id'], axis=1))

    # sorting the songs based on similarity score
    spotify_df.sort_values(by='sim', ascending=False, inplace=True, kind="mergesort")
    spotify_df.reset_index(drop=True, inplace=True)

    return spotify_df.head(10)


"""
    begin function gets passed on to app.py and executes all the above function and finally returns
    a list of data to be displayed onto results.html 
"""


def begin(song_title, song_artist):
    user_track = fetch_track(song_title, song_artist)

    if user_track is None:
        return None, None, None, None, None, None

    else:
        """ 
            Reading in the csv file and converting the genres column of the spotify dataset from string to a 
            list using regex expressions. Replacing spaces in user_tracks genres column
            with underscore 
            ( eg: hip hop ---> hip_hop )
        """

        tracks_df = pd.read_csv('tracks_with_genres_v4.csv')
        tracks_df.rename({'consolidates_genre_lists': 'genres'}, axis=1, inplace=True)
        tracks_df['genres'] = tracks_df['genres'].apply(lambda x: re.findall(r"'([^']*)'", str(x)))

        user_track['genres'] = user_track['genres'].apply(
            lambda x: [re.sub(' ', '_', i) for i in re.findall(r"'([^']*)'", str(x))])
        user_song_name = user_track.at[0, 'name']
        user_song_artist = user_track.at[0, 'artists']
        user_song_id = user_track.at[0, 'id']

        # dropping columns that are present in user_tracks but not in tracks_df and vice versa
        user_track.drop(['track_href', 'analysis_url', 'uri', 'type'], inplace=True, axis=1)
        tracks_df.drop(['explicit', 'release_date'], inplace=True, axis=1)

        """
            merging user_tracks and tracks_df(spotify dataset) because i need to create feature matrix
            later on which will help us to compute similarity
        """
        tracks_df = pd.concat([user_track, tracks_df], ignore_index=True)

        # removing duplicate songs from the dataset
        tracks_df.drop_duplicates(subset=['artists', 'name'], keep='first', inplace=True, ignore_index=True)
        tracks_df.drop_duplicates(subset=['id'], keep='first', inplace=True, ignore_index=True)

        # normalizing popularity and tempo column of the spotify dataset to range [0,1]
        tracks_df['popularity'] /= 100
        normalize(tracks_df, 'tempo')
        complete_feature_set = create_feature_set(tracks_df)

        # separating user track and remaining spotify dataset songs as we need to compute similarity between them
        final_spotify_feature_set = complete_feature_set[complete_feature_set['id'] != user_song_id]
        final_user_track_feature = complete_feature_set[complete_feature_set['id'] == user_song_id]
        tracks_recommend = generate_recommendations(final_spotify_feature_set.copy(), final_user_track_feature)

        track_name = []
        track_artist = []
        track_url = []
        album_image = []

        # returning the information for a given track using its id
        def recommend(track_id):
            return spotify.track(track_id=track_id)

        track_ids = [tracks_recommend.at[i, 'id']
                     for i in range(len(tracks_recommend))]

        # retrieving information for recommended tracks and using multiprocessing library
        # to run API requests in parallel
        with Pool(5) as pool:
            for res in pool.map(recommend, track_ids):
                track_url.append(res['external_urls'])
                track_name.append(res['name'])
                track_artist.append(res['artists'][0]['name'])
                album_image.append(res['album']['images'][0]['url'])

        return track_name, track_artist, track_url, album_image, user_song_name, user_song_artist
