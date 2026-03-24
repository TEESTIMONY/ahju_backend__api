from django.contrib import admin

from .models import CartItem, Product, UserAnalyticsDaily, UserAppearance, UserContactLead, UserLink, UserPortfolioItem


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ("name", "category", "price", "old_price", "stock_quantity", "is_active")
    list_filter = ("category", "is_active")
    search_fields = ("name", "slug", "category")
    ordering = ("name",)
    fieldsets = (
        (
            "Basic",
            {
                "fields": (
                    "name",
                    "slug",
                    "category",
                    "description",
                )
            },
        ),
        (
            "Pricing & Stock",
            {
                "fields": (
                    "price",
                    "old_price",
                    "stock_quantity",
                    "is_active",
                )
            },
        ),
        (
            "Media",
            {
                "fields": (
                    "image",
                    "image_url",
                    "gallery_images",
                )
            },
        ),
    )


@admin.register(CartItem)
class CartItemAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "session_key", "product", "quantity", "updated_at")
    search_fields = ("user__username", "session_key", "product__name")
    list_filter = ("updated_at",)
    ordering = ("-updated_at",)


@admin.register(UserAnalyticsDaily)
class UserAnalyticsDailyAdmin(admin.ModelAdmin):
    list_display = ("user", "date", "views", "clicks", "card_taps")
    list_filter = ("date",)
    search_fields = ("user__username", "user__email")
    ordering = ("-date",)


@admin.register(UserAppearance)
class UserAppearanceAdmin(admin.ModelAdmin):
    list_display = ("user", "display_name", "selected_theme", "name_font")
    search_fields = ("user__username", "user__email", "display_name")


@admin.register(UserLink)
class UserLinkAdmin(admin.ModelAdmin):
    list_display = ("user", "title", "url", "clicks", "is_active", "sort_order")
    list_filter = ("is_active",)
    search_fields = ("user__username", "user__email", "title", "url")
    ordering = ("user", "sort_order", "-created_at")


@admin.register(UserContactLead)
class UserContactLeadAdmin(admin.ModelAdmin):
    list_display = ("user", "name", "email", "phone", "source", "created_at")
    list_filter = ("source", "created_at")
    search_fields = ("user__username", "user__email", "name", "email", "phone")
    ordering = ("-created_at",)



@admin.register(UserPortfolioItem)
class UserPortfolioItemAdmin(admin.ModelAdmin):
    list_display = ("user", "kind", "title", "is_active", "sort_order", "created_at")
    list_filter = ("kind", "is_active")
    search_fields = ("user__username", "user__email", "title", "source_url")
    ordering = ("user", "sort_order", "-created_at")
