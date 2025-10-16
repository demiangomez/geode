import { CameraIcon, UserIcon } from "@heroicons/react/24/outline";
import React, { useState, useRef, Dispatch, SetStateAction } from "react";

type Props = {
    edit: boolean;
    hasImage: boolean;
    defaultImage?: string;
    image: string | null;
    setImage: Dispatch<SetStateAction<string | null>>;
    setHasImage: Dispatch<SetStateAction<boolean>>;
};

const ImageUploadCircle = ({
    edit,
    image,
    hasImage,
    setImage,
    setHasImage,
}: Props) => {
    const [isDragging, setIsDragging] = useState(false);
    const fileInputRef = useRef<HTMLInputElement>(null);

    const handleDragOver = (e: React.DragEvent<HTMLDivElement>) => {
        e.preventDefault();
        setIsDragging(true);
    };

    const handleDragLeave = (e: React.DragEvent<HTMLDivElement>) => {
        e.preventDefault();
        setIsDragging(false);
    };

    const handleDrop = (e: React.DragEvent<HTMLDivElement>) => {
        e.preventDefault();
        setIsDragging(false);

        if (e.dataTransfer && e.dataTransfer.files.length > 0) {
            const file = e.dataTransfer.files[0];

            if (file.type.startsWith("image/")) {
                const reader = new FileReader();
                reader.onload = (e) => {
                    const result =
                        e.target && typeof e.target.result === "string"
                            ? e.target.result
                            : undefined;
                    if (result) {
                        setImage(result);
                        setHasImage(true);
                    }
                };
                reader.readAsDataURL(file);
            }
        }
    };

    const handleClick = () => {
        fileInputRef.current?.click();
    };

    const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
        const file = e.target.files?.[0];
        if (file && file.type.startsWith("image/")) {
            const reader = new FileReader();
            reader.onload = (e) => {
                if (typeof e.target?.result === "string") {
                    setImage(e.target.result);
                    setHasImage(true);
                }
            };
            reader.readAsDataURL(file);
        }
    };

    const formatImageUrl = (imageData: string | null | undefined) => {
        if (!imageData) return null;

        // Si ya tiene el prefijo data:image, devolverla tal como está
        if (imageData.startsWith("data:image")) {
            return imageData;
        }

        // Si es solo base64, agregar el prefijo
        return `data:image/jpeg;base64,${imageData}`;
    };

    const handleGlobalDragOver = (e: DragEvent) => {
        e.preventDefault();
    };

    const handleGlobalDrop = (e: DragEvent) => {
        e.preventDefault();
    };

    React.useEffect(() => {
        window.addEventListener("dragover", handleGlobalDragOver);
        window.addEventListener("drop", handleGlobalDrop);

        return () => {
            window.removeEventListener("dragover", handleGlobalDragOver);
            window.removeEventListener("drop", handleGlobalDrop);
        };
    }, []);

    return (
        <div className="flex items-center justify-center relative mb-10">
            {image && !edit ? (
                <div
                    className={`relative w-32 h-32 rounded-full cursor-pointer bg-gray-400 shadow-lg bg-cover bg-center `}
                    style={{ backgroundImage: `url(${formatImageUrl(image)})` }}
                ></div>
            ) : !image && !edit ? (
                <UserIcon
                    className={`rounded-full text-gray-600 size-32  "backdrop-blur-sm bg-white/30 `}
                />
            ) : (
                <div
                    className={`relative w-32 h-32 rounded-full cursor-pointer bg-gray-100 shadow-lg 
                                ${isDragging ? "bg-transparent" : ""} 
                                ${hasImage ? "bg-cover bg-center" : ""}`}
                    style={
                        hasImage || !image
                            ? {
                                  backgroundImage: `url(${formatImageUrl(image)})`,
                              }
                            : {}
                    }
                    onDragOver={handleDragOver}
                    onDragLeave={handleDragLeave}
                    onDrop={handleDrop}
                    onClick={handleClick}
                >
                    <div
                        className={`absolute inset-0 border-4 border-dashed border-gray-300 rounded-full transition-opacity duration-200 
                    ${isDragging ? "animate-spin opacity-100" : hasImage ? "opacity-0" : "opacity-100"}`}
                    />

                    <div className="absolute inset-0 flex items-center justify-center text-gray-500 text-center px-8 transition-opacity duration-200 opacity-75">
                        <span className="text-sm flex flex-col items-center">
                            <CameraIcon className="size-14 z-10 text-gray-600 opacity-75" />
                            Select or drag a photo
                        </span>
                    </div>

                    <input
                        ref={fileInputRef}
                        type="file"
                        accept="image/*"
                        className="hidden"
                        onChange={handleFileChange}
                    />
                </div>
            )}
        </div>
    );
};

export default ImageUploadCircle;
