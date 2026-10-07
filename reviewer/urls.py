from django.urls import path
# from .api import ArticleDetailView, ArticleListView
from .api import BiddingUpdateView, BiddingView, ReviewPublishView, ReviewUpdateDraftView, ReviewUpdatePublishedView, ReviewerBidsView, ReviewerDetailView, ReviewView, ReviewDetailView, ReviewsArticleView, ReviewByReviewerView, ReviewsByReviewerIdView,ReviewVersionsView, ReviewerAssignmentsView, ReviewerInvitationsView, ReviewerInvitationDetailView, AcceptInvitationView, RejectInvitationView, ReviewerConferencesView, ReviewerArticleAssignmentView, InvitationNotificationsView
urlpatterns = [
    path('reviewer/invitation-notifications/', InvitationNotificationsView.as_view(), name='reviewer-invitation-notifications'),
    path('reviewer/articles/<int:article_id>/assignment/', ReviewerArticleAssignmentView.as_view(), name='reviewer-article-assignment'),
    path('reviewer/conferences/', ReviewerConferencesView.as_view(), name='reviewer-conferences'),
    path('reviewer/invitations/', ReviewerInvitationsView.as_view(), name='reviewer-invitations'),
    path('reviewer/invitations/<int:id>/', ReviewerInvitationDetailView.as_view(), name='reviewer-invitation-detail'),
    path('reviewer/invitations/<int:id>/accept/', AcceptInvitationView.as_view(), name='reviewer-invitation-accept'),
    path('reviewer/invitations/<int:id>/reject/', RejectInvitationView.as_view(), name='reviewer-invitation-reject'),
    # path('articles/', ArticleListView.as_view()),
    # path('articles/<int:pk>/', ArticleDetailView.as_view()),
    path('bidding/', BiddingView.as_view()),
    path('bidding/<int:id>/', BiddingUpdateView.as_view(), name='bidding-update'),
    path('bids/', ReviewerBidsView.as_view(), name='reviewer-bids'),
    path('reviewers/<int:id>/', ReviewerDetailView.as_view(), name='reviewer-detail'),
    path('reviews/',ReviewView.as_view(),name="create-review"),
    path('reviews/<int:articleId>/',ReviewDetailView.as_view(),name="review-detail"),
    path('reviews/reviewer/<int:reviewerId>/', ReviewsByReviewerIdView.as_view(), name='review-by-reviewer-id'),
    path('reviews/<int:idReview>/versions/', ReviewVersionsView.as_view(), name='review-versions'),
    path('reviews/<int:id>/updateDraft/',ReviewUpdateDraftView.as_view(),name="review-update-draft"),
    path('reviews/<int:id>/updatePublished/',ReviewUpdatePublishedView.as_view(),name="review-update-published"),
    path('reviews/<int:id>/publish/', ReviewPublishView.as_view(), name='review-publish'),
    path('article/<int:article_id>/reviews/',ReviewsArticleView.as_view(),name="reviews-article"),
    path('reviews/<int:articleId>/<int:reviewerId>/', ReviewByReviewerView.as_view(), name='review-by-reviewer'),
    path('reviewer/assignments/', ReviewerAssignmentsView.as_view(), name='reviewer-assignments'),

]