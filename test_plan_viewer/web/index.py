"""HTML page routes.

Keeping template delivery in a Blueprint gives the application entrypoint a
small, explicit registration surface while API domains migrate independently.
"""

from flask import Blueprint, render_template


index_blueprint = Blueprint("index", __name__)


@index_blueprint.get("/")
def index():
    return render_template("index.html")
