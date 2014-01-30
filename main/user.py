# -*- coding: utf-8 -*-
import copy

from flask.ext import wtf
from google.appengine.ext import deferred
from google.appengine.ext import ndb
import flask

import auth
import model
import util

from main import app


###############################################################################
# User List
###############################################################################
@app.route('/_s/user/', endpoint='user_list_service')
@app.route('/user/')
@auth.admin_required
def user_list():
  user_dbs, more_cursor = util.retrieve_dbs(
      model.User.query(),
      limit=util.param('limit', int),
      cursor=util.param('cursor'),
      order=util.param('order') or '-created',
      name=util.param('name'),
      admin=util.param('admin', bool),
    )

  if flask.request.path.startswith('/_s/'):
    return util.jsonify_model_dbs(user_dbs, more_cursor)

  return flask.render_template(
      'user/user_list.html',
      html_class='user-list',
      title='User List',
      user_dbs=user_dbs,
      more_url=util.generate_more_url(more_cursor),
      has_json=True,
    )


###############################################################################
# User Update
###############################################################################
class UserUpdateForm(wtf.Form):
  username = wtf.StringField('Username',
      [wtf.validators.required(), wtf.validators.length(min=3)],
      filters=[util.email_filter],
    )
  name = wtf.StringField('Name',
      [wtf.validators.required()], filters=[util.strip_filter],
    )
  email = wtf.StringField('Email',
      [wtf.validators.optional(), wtf.validators.email()],
      filters=[util.email_filter],
    )
  admin = wtf.BooleanField('Admin')
  active = wtf.BooleanField('Active')


@app.route('/user/<int:user_id>/update/', methods=['GET', 'POST'])
@auth.admin_required
def user_update(user_id):
  user_db = model.User.get_by_id(user_id)
  if not user_db:
    flask.abort(404)

  form = UserUpdateForm(obj=user_db)
  if form.validate_on_submit():
    if not util.is_valid_username(form.username.data):
      form.username.errors.append('This username is invalid.')
    elif not is_username_available(form.username.data, user_db):
      form.username.errors.append('This username is taken.')
    else:
      form.populate_obj(user_db)
      if auth.current_user_id() == user_db.key.id():
        user_db.admin = True
        user_db.active = True
      user_db.put()
      return flask.redirect(flask.url_for('user_list', order='-modified'))

  if flask.request.path.startswith('/_s/'):
    return util.jsonify_model_db(user_db)

  return flask.render_template(
      'user/user_update.html',
      title=user_db.name,
      html_class='user-update',
      form=form,
      user_db=user_db,
    )


###############################################################################
# User Delete
###############################################################################
@app.route('/_s/user/delete/', methods=['DELETE'])
@auth.admin_required
def user_delete_service():
  user_keys = util.param('user_keys', list)
  user_db_keys = [ndb.Key(urlsafe=k) for k in user_keys]
  delete_user_dbs(user_db_keys)
  return flask.jsonify({
      'result': user_keys,
      'status': 'success',
    })


def delete_user_dbs(user_db_keys):
  user_dbs = ndb.get_multi(user_db_keys)
  for user_db in user_dbs:
    user_db.active = False
    delete_user_task(user_db.key)
  ndb.put_multi(user_dbs)


def delete_user_task(user_key, more_cursor=None):
  resource_keys, more_cursor = util.retrieve_dbs(
      model.Resource.query(),
      user_key=user_key,
      cursor=more_cursor,
      keys_only=True,
    )
  if resource_keys:
    ndb.delete_multi(resource_keys)
  if more_cursor:
    deferred.defer(move_resources_task, user_key, more_cursor)
  else:
    user_key.delete()


###############################################################################
# User Merge
###############################################################################
class UserMergeForm(wtf.Form):
  user_key = wtf.StringField('User Key', [wtf.validators.required()])
  user_keys = wtf.StringField('User Keys', [wtf.validators.required()])
  username = wtf.StringField('Username', [wtf.validators.optional()])
  name = wtf.StringField('Merged Name',
      [wtf.validators.required()], filters=[util.strip_filter],
    )
  email = wtf.StringField('Merged Email',
      [wtf.validators.optional(), wtf.validators.email()],
      filters=[util.email_filter],
    )


@app.route('/_s/user/merge/')
@app.route('/user/merge/', methods=['GET', 'POST'])
@auth.admin_required
def user_merge():
  user_keys = util.param('user_keys', list)
  if not user_keys:
    flask.abort(400)

  user_db_keys = [ndb.Key(urlsafe=k) for k in user_keys]
  user_dbs = ndb.get_multi(user_db_keys)
  if len(user_dbs) < 2:
    flask.abort(400)

  if flask.request.path.startswith('/_s/'):
    return util.jsonify_model_dbs(user_dbs)

  user_dbs.sort(key=lambda user_db: user_db.created)
  merged_user_db = user_dbs[0]
  auth_ids = []
  is_admin = False
  is_active = False
  for user_db in user_dbs:
    auth_ids.extend(user_db.auth_ids)
    auth_ids.extend(user_db.auth_ids)
    auth_ids.extend(user_db.auth_ids)
    is_admin = is_admin or user_db.admin
    is_active = is_active or user_db.active
    if user_db.key.urlsafe() == util.param('user_key'):
      merged_user_db = user_db

  auth_ids = sorted(list(set(auth_ids)))
  merged_user_db.admin = is_admin
  merged_user_db.active = is_active

  form_obj = copy.deepcopy(merged_user_db)
  form_obj.user_key = merged_user_db.key.urlsafe()
  form_obj.user_keys = ','.join(user_keys)

  form = UserMergeForm(obj=form_obj)
  if form.validate_on_submit():
    form.populate_obj(merged_user_db)
    merged_user_db.auth_ids = auth_ids
    merged_user_db.put()

    depricated_keys = [key for key in user_db_keys if key != merged_user_db.key]
    merge_user_dbs(merged_user_db, depricated_keys)
    return flask.redirect(
        flask.url_for('user_update', user_id=merged_user_db.key.id()),
      )

  return flask.render_template(
      'user/user_merge.html',
      title='Merge Users',
      html_class='user-merge',
      user_dbs=user_dbs,
      merged_user_db=merged_user_db,
      form=form,
      auth_ids=auth_ids,
    )


@ndb.transactional(xg=True)
def merge_user_dbs(user_db, depricated_keys):
  # TODO: Merge possible user data before handlining deprecated users
  depricated_dbs = ndb.get_multi(depricated_keys)
  for depricated_db in depricated_dbs:
    depricated_db.auth_ids = []
    depricated_db.active = False
    if not depricated_db.username.startswith('_'):
      depricated_db.username = '_%s' % depricated_db.username
    deferred.defer(move_resources_task, user_db.key, depricated_db.key)
  ndb.put_multi(depricated_dbs)


def move_resources_task(user_key, depricated_key, more_cursor=None):
  resource_dbs, more_cursor = util.retrieve_dbs(
      model.Resource.query(),
      user_key=depricated_key,
      cursor=more_cursor,
    )
  for resource_db in resource_dbs:
    resource_db.user_key = user_key
  ndb.put_multi(resource_dbs)
  if more_cursor:
    deferred.defer(move_resources_task, user_key, depricated_key, more_cursor)


########################################################
# Helpers
###############################################################################
def is_username_available(username, self_db=None):
  user_dbs, more_cursor = util.retrieve_dbs(
      model.User.query(),
      username=username,
      limit=2,
    )
  c = len(user_dbs)
  return not (c == 2 or c == 1 and self_db and self_db.key != user_dbs[0].key)