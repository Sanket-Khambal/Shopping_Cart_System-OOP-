from django.http import HttpResponseBadRequest
from django.shortcuts import render, redirect
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth import login,logout
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.forms import AuthenticationForm
from .models import Cart, Product, CartItem,UserProfile
from Shopping_cart_system.commands import AddToCartCommand,ViewProductsCommand,ViewCartCommand,RemoveFromCartCommand
from Shopping_cart_system.strategies import DefaultPriceCalculationStrategy,DiscountPriceCalculationStrategy,CouponDiscountStrategy,CreditCardPaymentStrategy,PayPalPaymentStrategy,QuantityBasedDiscountStrategy
from Shopping_cart_system.observers import BudgetExceededObserver,PriceDecreasedObserver


# Used render for rendering pages

def home(request):
    return render(request,'home.html')         

def register_user(request):
    if request.method == 'POST':
        form = UserCreationForm(request.POST)                # form to get details from the frontend
        if form.is_valid():
            user = form.save()
            login(request, user)
            return redirect('home')  
    else:
        form = UserCreationForm()
    
    return render(request, 'register.html', {'form': form})

def login_user(request):
    form = AuthenticationForm(request, data=request.POST or None)           # Used authentication form to authenticate users

    if request.method == 'POST':
        form = AuthenticationForm(request, data=request.POST)
        if form.is_valid():
            user = form.get_user()
            login(request, user)
            return redirect('user_home')  
        form = AuthenticationForm()
    
    return render(request, 'login.html', {'form': form})

def categories(request):
    unique_categories = Product.objects.values('category').distinct()        # getting distinct strategies
    return render(request, 'categories.html', {'categories': unique_categories})

@login_required
def set_budget(request):
    if request.method == 'POST':
        budget = request.POST.get('budget')
        user_profile, created = UserProfile.objects.get_or_create(user=request.user)  # getting user instance
        user_profile.budget = budget
        user_profile.save()
        return redirect('user_home')  
    else:
        return redirect('user_home')

def view_products_by_category(request, category):
    # Using the ViewProductsCommand to get products for the specified category
    view_command = ViewProductsCommand(category)
    products = view_command.execute()

    data = {
        'category': category,
        'products': products,                     # passing data to frontend
    }

    return render(request, 'products_by_category.html', data)

def view_cart(request):
    user_cart, created = Cart.objects.get_or_create(user=request.user)  # getting cart instance

    view_cart_command = ViewCartCommand(cart=user_cart) # Viewing cart using view_cart_command
    cart_items = view_cart_command.execute()

    cart_items_data = [
        {
            'product_name': item.product.name,
            'quantity': item.quantity,
            'total_price': item.product.price * item.quantity,
            'id':item.product.id,
            'category':item.product.category
        }
        for item in cart_items
    ]

    total_price = sum(item['total_price'] for item in cart_items_data) # Calculating total price

    context = {
        'cart_items_data': cart_items_data,
        'total_price': total_price,                              # passing data to frontend
    }

    return render(request, 'cart.html', context)


def add_to_cart(request, product_id):
    # getting the user's cart
    user_cart, created = Cart.objects.get_or_create(user=request.user)

    # Getting the product based on the product_id
    product = Product.objects.get(pk=product_id)
    category = product.category
    
    # Adding to cart using add_to_cart_command
    if request.method == 'POST':
        quantity = int(request.POST.get('quantity', 1))
        add_to_cart_command = AddToCartCommand(cart=user_cart, product=product, quantity=quantity)
        add_to_cart_command.execute()
        return redirect('products_by_category',category=category) 
    
def remove_from_cart(request, product_id, quantity):
    # Removing from cart using remove_to_cart command
    product = Product.objects.get(pk=product_id)  # getting products
    user_cart = Cart.objects.get(user=request.user) 
    remove_command = RemoveFromCartCommand(cart=user_cart, product=product, quantity=quantity)
    remove_command.execute()
    return redirect('view_cart') 
    

def checkout(request):
    print(f"User object in request: {request.user}")
    # Retrieve the user's cart
    user_cart, created = Cart.objects.get_or_create(user=request.user)

    # View the cart to get the cart items
    view_cart_command = ViewCartCommand(cart=user_cart)
    cart_items = view_cart_command.execute()

    # Apply quantity-based discount strategy if there are more than four products
    quantity_discount_threshold = 4
    if len(cart_items) > quantity_discount_threshold:
        discount_strategy = QuantityBasedDiscountStrategy(DefaultPriceCalculationStrategy(), discount_threshold=quantity_discount_threshold, discount_percentage=15)
        discount_message = f'Quantity discount applied! Get {discount_strategy.discount_percentage}% off for purchasing more than {quantity_discount_threshold} products.'
    else:
        discount_strategy = DiscountPriceCalculationStrategy(DefaultPriceCalculationStrategy(), discount_percentage=10)
        discount_message = 'Default discount applied!'

    # Apply coupon discount if a coupon code is provided
    coupon_code = request.GET.get('coupon_code')
    if coupon_code:
        # Checking if the provided coupon code is valid
        coupon_strategy = CouponDiscountStrategy(discount_strategy, coupon_code, discount_percentage=20)
        if coupon_strategy.is_coupon_valid():
            total_price = sum(coupon_strategy.calculate_price(item.product, item.quantity) for item in cart_items)
            messages.success(request, f'Coupon "{coupon_code}" applied successfully! {discount_message}')
        else:
            total_price = sum(discount_strategy.calculate_price(item.product, item.quantity) for item in cart_items)
            messages.error(request, f'Invalid coupon code. {discount_message}')
    else:
        total_price = sum(discount_strategy.calculate_price(item.product, item.quantity) for item in cart_items)
        messages.success(request, discount_message)

    # Notify observers (BudgetExceededObserver)
    observers = [BudgetExceededObserver(),PriceDecreasedObserver()]
    for observer in observers:
        observer.notify(request, total_price)


    data = {
        'cart_items': cart_items,
        'total_price': total_price,
    }

    return render(request, 'checkout.html', data)

def process_payment(request):
    user_cart,created = Cart.objects.get_or_create(user=request.user)
    #print(created)

    payment_method = request.POST.get('payment_method')
    total_price = request.POST.get('total_price')

    # Payment strategy
    if payment_method == 'credit card':
        payment_strategy = CreditCardPaymentStrategy()
    elif payment_method == 'paypal':
        payment_strategy = PayPalPaymentStrategy()
    else:
        # Handling invalid payment method
        return render(request, 'checkout.html', {'error_message': 'Invalid payment method'})

    # Processing payment
    payment_result = payment_strategy.process_payment(total_price)

    # Clearing the cart after payment is done
    user_cart.cartitem_set.all().delete()

    return render(request, 'order_placed.html', {'data': payment_result})

