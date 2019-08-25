from django.conf import settings
from django.http import HttpResponse, JsonResponse, HttpResponseBadRequest
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticatedOrReadOnly
import json
import time
from api.notifications.dispatch import notify_comment
from api.firebase_auth.authentication import TokenAuthentication
from api.spam.classifier import classify_text
from api.spam.views import get_spam_report_data
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer

db = settings.FIREBASE.database()


def get_comments_from_thread(thread):
    """
    This function should be encapsulated inside a Comment model

    """
    thread_data = db.child('comments').child(thread).get().val()
    if not thread_data or not thread_data.get('comments', False):
        return {'comments': {}, 'userData': {}}
    user_data = {}

    for user in thread_data['participants']:
        tmp_user = db.child('users').child(user).get().val()
        print(user)
        user_data[user] = dict(tmp_user)
    response = {}
    response['userData'] = user_data
    response['comments'] = thread_data['comments']
    for comment_uuid in thread_data['comments'].keys():
        spam_report_data = get_spam_report_data(comment_uuid)
        response['comments'][comment_uuid]['spam'] = spam_report_data

    return response

class CommentView(APIView):
    authentication_classes = (TokenAuthentication,)
    permission_classes = (IsAuthenticatedOrReadOnly,)

    def get(self, request):
        thread = request.GET.get('thread', False)
        if not thread:
            return HttpResponseBadRequest('No thread specified')

        response = get_comments_from_thread(thread)
        return JsonResponse(response, safe=False)

    def post(self, request):
        try:
            commentData = json.loads(json.loads(request.body.decode()).get('commentData'))
            thread = commentData['thread']
            text = commentData['text']
        except:
            return HttpResponseBadRequest('Bad Request')

        uid = str(request.user)
        timestamp = time.time()*1000
        comment = {
            'text': text,
            'user': uid,
            'timestamp': timestamp,
        }
        val = db.child('comments').child(thread).child('comments').push(comment)

        db.child('comments').child(thread).child('participants').update({
            uid: True
        })
        classify_text(text, val['name'])
        
        user_name = request.user.name
        user_picture = request.user.user_picture
        
        notify_comment(sender_uid=uid, datetime=time.time()*1000, 
            event_id=thread, user_text=text,
            user_name=user_name, user_picture=user_picture)

        channel_layer = get_channel_layer()
        comments_data = {
            "type": "comments_message",
            "message": {
                'actionType': 'WS_NEW_COMMENT_RECEIVED',
                'data': {
                    'comments': {},
                    'userData': {}
                }
            }
        }
        comments_data['message']['data']['comments'][val['name']] = {
            'text': text,
            'spam': {
                'uuid': val['name'],
                'count': 0,
                'toxic': 'null',
            },
            'user': uid,
            'timestamp': timestamp
        }
        comments_data['message']['data']['userData'][uid] = {
            'photoURL': user_picture,
            'displayName': user_name
        }
        room_name = 'comments_%s' % thread
        print('room_name', room_name)

        async_to_sync(channel_layer.group_send)(
            room_name,
            comments_data
        )
        
        return JsonResponse({'id': val['name']}, safe=False)